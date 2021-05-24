# -*- coding: utf-8 -*-
"""
This modules contains the Dropbox API client. It wraps calls to the Dropbox Python SDK
and handles exceptions, chunked uploads or downloads, etc.
"""

# system imports
import errno
import os
import os.path as osp
import time
import logging
import contextlib
from datetime import datetime, timezone
from typing import (
    Callable,
    Union,
    Any,
    Type,
    Tuple,
    List,
    Iterator,
    TypeVar,
    Optional,
    TYPE_CHECKING,
)

# external imports
import requests
from dropbox import (  # type: ignore
    Dropbox,
    create_session,
    files,
    sharing,
    users,
    exceptions,
    async_,
    auth,
    oauth,
)
from dropbox.stone_validators import ValidationError

# local imports
from . import __version__
from .oauth import OAuth2Session
from .errors import (
    MaestralApiError,
    SyncError,
    InsufficientPermissionsError,
    PathError,
    FileReadError,
    InsufficientSpaceError,
    FileConflictError,
    FolderConflictError,
    ConflictError,
    UnsupportedFileError,
    RestrictedContentError,
    NotFoundError,
    NotAFolderError,
    IsAFolderError,
    FileSizeError,
    OutOfMemoryError,
    BadInputError,
    DropboxAuthError,
    TokenExpiredError,
    TokenRevokedError,
    CursorResetError,
    DropboxServerError,
    NotLinkedError,
    InvalidDbidError,
    SharedLinkError,
    DropboxConnectionError,
)
from .config import MaestralState
from .constants import DROPBOX_APP_KEY
from .utils import natural_size, chunks, clamp

if TYPE_CHECKING:
    from .database import SyncEvent


__all__ = [
    "CONNECTION_ERRORS",
    "DropboxClient",
    "dropbox_to_maestral_error",
    "os_to_maestral_error",
    "convert_api_errors",
]


# type definitions
LocalError = Union[MaestralApiError, OSError]
WriteErrorType = Type[
    Union[
        SyncError,
        InsufficientPermissionsError,
        PathError,
        InsufficientSpaceError,
        FileConflictError,
        FolderConflictError,
        ConflictError,
        FileReadError,
    ]
]
LookupErrorType = Type[
    Union[
        SyncError,
        UnsupportedFileError,
        RestrictedContentError,
        NotFoundError,
        NotAFolderError,
        IsAFolderError,
        PathError,
    ]
]
SessionLookupErrorType = Type[
    Union[
        SyncError,
        FileSizeError,
    ]
]
PaginationResultType = Union[sharing.ListSharedLinksResult, files.ListFolderResult]
FT = TypeVar("FT", bound=Callable[..., Any])

_major_minor_version = ".".join(__version__.split(".")[:2])
USER_AGENT = f"Maestral/v{_major_minor_version}"


CONNECTION_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.RetryError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    ConnectionError,
)


@contextlib.contextmanager
def convert_api_errors(
    dbx_path: Optional[str] = None, local_path: Optional[str] = None
) -> Iterator[None]:
    """
    A context manager that catches and re-raises instances of :class:`OSError` and
    :class:`dropbox.exceptions.DropboxException` as
    :class:`maestral.errors.MaestralApiError` or :class:`ConnectionError`.

    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    """

    try:
        yield
    except (exceptions.DropboxException, ValidationError, requests.HTTPError) as exc:
        raise dropbox_to_maestral_error(exc, dbx_path, local_path)
    # Catch connection errors first, they may inherit from OSError.
    except CONNECTION_ERRORS:
        raise DropboxConnectionError(
            "Cannot connect to Dropbox",
            "Please check you internet connection and try again later.",
        )
    except OSError as exc:
        if exc.errno == errno.EPROTOTYPE:
            # Can occur on macOS, see https://bugs.python.org/issue33450.
            raise DropboxConnectionError(
                "Cannot connect to Dropbox",
                "Please check you internet connection and try again later.",
            )
        else:
            raise os_to_maestral_error(exc, dbx_path, local_path)


class DropboxClient:
    """Client for the Dropbox SDK

    This client defines basic methods to wrap Dropbox Python SDK calls, such as
    creating, moving, modifying and deleting files and folders on Dropbox and
    downloading files from Dropbox.

    All Dropbox SDK exceptions, OSErrors from the local file system API and connection
    errors will be caught and reraised as a subclass of
    :class:`maestral.errors.MaestralApiError`.

    This class can be used as a context manager to clean up any network resources from
    the API requests.

    :Example:

        >>> from maestral.client import DropboxClient
        >>> with DropboxClient("maestral") as client:
        ...     res = client.list_folder("/")
        >>> print(res.entries)

    :param config_name: Name of config file and state file to use.
    :param timeout: Timeout for individual requests. Defaults to 100 sec if not given.
    :param session: Optional requests session to use. If not given, a new session will
        be created with :func:`dropbox.dropbox_client.create_session`.
    """

    SDK_VERSION: str = "2.0"

    _dbx: Optional[Dropbox]

    def __init__(
        self,
        config_name: str,
        timeout: float = 100,
        session: Optional[requests.Session] = None,
    ) -> None:

        self.config_name = config_name
        self.auth = OAuth2Session(config_name)

        self._logger = logging.getLogger(__name__)

        self._timeout = timeout
        self._session = session or create_session()
        self._backoff_until = 0
        self._dbx = None
        self._state = MaestralState(config_name)

    # ---- linking API -----------------------------------------------------------------

    @property
    def dbx(self) -> Dropbox:
        """The underlying Dropbox SDK instance."""
        if not self.linked:
            raise NotLinkedError(
                "No auth token set", "Please link a Dropbox account first."
            )

        return self._dbx

    @property
    def linked(self) -> bool:
        """
        Indicates if the client is linked to a Dropbox account (read only). This will
        block until the user's keyring is unlocked to load the saved auth token.

        :raises KeyringAccessError: if keyring access fails.
        """

        if self._dbx:
            return True

        elif self.auth.linked:  # this will trigger keyring access on first call

            if self.auth.token_access_type == "legacy":
                self._init_sdk_with_token(access_token=self.auth.access_token)
            else:
                self._init_sdk_with_token(refresh_token=self.auth.refresh_token)

            return True

        else:
            return False

    def get_auth_url(self) -> str:
        """
        Returns a URL to authorize access to a Dropbox account. To link a Dropbox
        account, retrieve an auth token from the URL and link Maestral by calling
        :meth:`link` with the provided token.

        :returns: URL to retrieve an OAuth token.
        """
        return self.auth.get_auth_url()

    def link(self, token: str) -> int:
        """
        Links Maestral with a Dropbox account using the given access token. The token
        will be stored for future usage as documented in the :mod:`oauth` module.

        :param token: OAuth token for Dropbox access.
        :returns: 0 on success, 1 for an invalid token and 2 for connection errors.
        """

        res = self.auth.verify_auth_token(token)

        if res == self.auth.Success:
            self.auth.save_creds()

            self._init_sdk_with_token(
                refresh_token=self.auth.refresh_token,
                access_token=self.auth.access_token,
                access_token_expiration=self.auth.access_token_expiration,
            )

            try:
                self.get_account_info()
                self.get_space_usage()
            except ConnectionError:
                pass

        return res

    def unlink(self) -> None:
        """
        Unlinks the Dropbox account.

        :raises KeyringAccessError: if keyring access fails.
        :raises DropboxAuthError: if we cannot authenticate with Dropbox.
        """

        with convert_api_errors():
            self.dbx.auth_token_revoke()
            self.auth.delete_creds()

    def _init_sdk_with_token(
        self,
        refresh_token: Optional[str] = None,
        access_token: Optional[str] = None,
        access_token_expiration: Optional[datetime] = None,
    ) -> None:
        """
        Sets the access tokens for the Dropbox API. This will create a new SDK instance
        with new tokens.

        :param refresh_token: Long-lived refresh token to generate new access tokens.
        :param access_token: Short-lived auth token.
        :param access_token_expiration: Expiry time of auth token.
        """

        if refresh_token or access_token:

            self._dbx = Dropbox(
                oauth2_refresh_token=refresh_token,
                oauth2_access_token=access_token,
                oauth2_access_token_expiration=access_token_expiration,
                app_key=DROPBOX_APP_KEY,
                session=self._session,
                user_agent=USER_AGENT,
                timeout=self._timeout,
            )
        else:
            self._dbx = None

    @property
    def account_id(self) -> Optional[str]:
        """The unique Dropbox ID of the linked account"""
        return self.auth.account_id

    # ---- session management ----------------------------------------------------------

    def close(self) -> None:
        """Cleans up all resources like the request session/network connection."""
        if self._dbx:
            self._dbx.close()

    def __enter__(self) -> "DropboxClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def clone(
        self,
        config_name: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Optional[requests.Session] = None,
    ) -> "DropboxClient":
        """
        Creates a new copy of the Dropbox client with the same defaults unless modified
        by arguments to :meth:`clone`.

        :param config_name: Name of config file and state file to use.
        :param timeout: Timeout for individual requests.
        :param session: Requests session to use.
        :returns: A new instance of DropboxClient.
        """

        session = session or self._session

        client = self.__class__(
            config_name or self.config_name,
            timeout or self._timeout,
            session,
        )

        if self._dbx:
            client._dbx = self._dbx.clone(session=session)

        return client

    def clone_with_new_session(self) -> "DropboxClient":
        """
        Creates a new copy of the Dropbox client with the same defaults but a new
        requests session.

        :returns: A new instance of DropboxClient.
        """
        return self.clone(session=create_session())

    # ---- SDK wrappers ----------------------------------------------------------------

    def get_account_info(self, dbid: Optional[str] = None) -> users.FullAccount:
        """
        Gets current account information.

        :param dbid: Dropbox ID of account. If not given, will get the info of the
            currently linked account.
        :returns: Account info.
        """

        with convert_api_errors():
            if dbid:
                res = self.dbx.users_get_account(dbid)
            else:
                res = self.dbx.users_get_current_account()

        if not dbid:
            # Save our own account info to config.
            if res.account_type.is_basic():
                account_type = "basic"
            elif res.account_type.is_business():
                account_type = "business"
            elif res.account_type.is_pro():
                account_type = "pro"
            else:
                account_type = ""

            self._state.set("account", "email", res.email)
            self._state.set("account", "display_name", res.name.display_name)
            self._state.set("account", "abbreviated_name", res.name.abbreviated_name)
            self._state.set("account", "type", account_type)

        return res

    def get_space_usage(self) -> users.SpaceUsage:
        """
        :returns: The space usage of the currently linked account.
        """
        with convert_api_errors():
            res = self.dbx.users_get_space_usage()

        # Query space usage type.
        if res.allocation.is_team():
            usage_type = "team"
        elif res.allocation.is_individual():
            usage_type = "individual"
        else:
            usage_type = ""

        # Generate space usage string.
        if res.allocation.is_team():
            used = res.allocation.get_team().used
            allocated = res.allocation.get_team().allocated
        else:
            used = res.used
            allocated = res.allocation.get_individual().allocated

        percent = used / allocated
        space_usage = f"{percent:.1%} of {natural_size(allocated)} used"

        # Save results to config.
        self._state.set("account", "usage", space_usage)
        self._state.set("account", "usage_type", usage_type)

        return res

    def get_metadata(self, dbx_path: str, **kwargs) -> Optional[files.Metadata]:
        """
        Gets metadata for an item on Dropbox or returns ``False`` if no metadata is
        available. Keyword arguments are passed on to Dropbox SDK files_get_metadata
        call.

        :param dbx_path: Path of folder on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_get_metadata.
        :returns: Metadata of item at the given path or ``None`` if item cannot be found.
        """

        try:
            with convert_api_errors(dbx_path=dbx_path):
                return self.dbx.files_get_metadata(dbx_path, **kwargs)
        except (NotFoundError, PathError):
            return None

    def list_revisions(
        self, dbx_path: str, mode: str = "path", limit: int = 10
    ) -> files.ListRevisionsResult:
        """
        Lists all file revisions for the given file.

        :param dbx_path: Path to file on Dropbox.
        :param mode: Must be 'path' or 'id'. If 'id', specify the Dropbox file ID
            instead of the file path to get revisions across move and rename events.
        :param limit: Maximum number of revisions to list.
        :returns: File revision history.
        """

        with convert_api_errors(dbx_path=dbx_path):
            mode = files.ListRevisionsMode(mode)
            return self.dbx.files_list_revisions(dbx_path, mode=mode, limit=limit)

    def restore(self, dbx_path: str, rev: str) -> files.FileMetadata:
        """
        Restore an old revision of a file.

        :param dbx_path: The path to save the restored file.
        :param rev: The revision to restore. Old revisions can be listed with
            :meth:`list_revisions`.
        :returns: Metadata of restored file.
        """

        with convert_api_errors(dbx_path=dbx_path):
            return self.dbx.files_restore(dbx_path, rev)

    def download(
        self,
        dbx_path: str,
        local_path: str,
        sync_event: Optional["SyncEvent"] = None,
        **kwargs,
    ) -> files.FileMetadata:
        """
        Downloads a file from Dropbox to given local path.

        :param dbx_path: Path to file on Dropbox or rev number.
        :param local_path: Path to local download destination.
        :param sync_event: If given, the sync event will be updated with the number of
            downloaded bytes.
        :param kwargs: Keyword arguments for the Dropbox API files_download endpoint.
        :returns: Metadata of downloaded item.
        """

        with convert_api_errors(dbx_path=dbx_path):

            dst_path_directory = osp.dirname(local_path)
            try:
                os.makedirs(dst_path_directory)
            except FileExistsError:
                pass

            md, http_resp = self.dbx.files_download(dbx_path, **kwargs)

            chunksize = 2 ** 13

            with open(local_path, "wb") as f:
                with contextlib.closing(http_resp):
                    for c in http_resp.iter_content(chunksize):
                        f.write(c)
                        if sync_event:
                            sync_event.completed = f.tell()

        # Dropbox SDK provides naive datetime in UTC.
        client_mod = md.client_modified.replace(tzinfo=timezone.utc)
        server_mod = md.server_modified.replace(tzinfo=timezone.utc)

        # Enforce client_modified < server_modified.
        timestamp = min(client_mod.timestamp(), server_mod.timestamp(), time.time())
        # Set mtime of downloaded file.
        os.utime(local_path, (time.time(), timestamp))

        return md

    def upload(
        self,
        local_path: str,
        dbx_path: str,
        chunk_size: int = 5 * 10 ** 6,
        sync_event: Optional["SyncEvent"] = None,
        **kwargs,
    ) -> files.FileMetadata:
        """
        Uploads local file to Dropbox.

        :param local_path: Path of local file to upload.
        :param dbx_path: Path to save file on Dropbox.
        :param kwargs: Keyword arguments for Dropbox SDK files_upload.
        :param chunk_size: Maximum size for individual uploads. If larger than 150 MB,
            it will be set to 150 MB.
        :param sync_event: If given, the sync event will be updated with the number of
            downloaded bytes.
        :returns: Metadata of uploaded file.
        """

        chunk_size = clamp(chunk_size, 10 ** 5, 150 * 10 ** 6)

        with convert_api_errors(dbx_path=dbx_path, local_path=local_path):

            size = osp.getsize(local_path)

            # Dropbox SDK takes naive datetime in UTC/
            mtime = osp.getmtime(local_path)
            mtime_dt = datetime.utcfromtimestamp(mtime)

            if size <= chunk_size:
                with open(local_path, "rb") as f:
                    md = self.dbx.files_upload(
                        f.read(), dbx_path, client_modified=mtime_dt, **kwargs
                    )
                    if sync_event:
                        sync_event.completed = f.tell()
                return md
            else:
                # Note: We currently do not support resuming interrupted uploads.
                # Dropbox keeps upload sessions open for 48h so this could be done in
                # the future.
                with open(local_path, "rb") as f:
                    data = f.read(chunk_size)
                    session_start = self.dbx.files_upload_session_start(data)
                    uploaded = f.tell()

                    cursor = files.UploadSessionCursor(
                        session_id=session_start.session_id, offset=uploaded
                    )
                    commit = files.CommitInfo(
                        path=dbx_path, client_modified=mtime_dt, **kwargs
                    )

                    if sync_event:
                        sync_event.completed = uploaded

                    while True:
                        try:

                            if size - f.tell() <= chunk_size:
                                # Finish upload session and return metadata.
                                data = f.read(chunk_size)
                                md = self.dbx.files_upload_session_finish(
                                    data, cursor, commit
                                )
                                if sync_event:
                                    sync_event.completed = sync_event.size
                                return md
                            else:
                                # Append to upload session.
                                data = f.read(chunk_size)
                                self.dbx.files_upload_session_append_v2(data, cursor)

                                uploaded = f.tell()
                                cursor.offset = uploaded

                                if sync_event:
                                    sync_event.completed = uploaded

                        except exceptions.DropboxException as exc:
                            error = getattr(exc, "error", None)
                            if (
                                isinstance(error, files.UploadSessionFinishError)
                                and error.is_lookup_failed()
                            ):
                                session_lookup_error = error.get_lookup_failed()
                            elif isinstance(error, files.UploadSessionLookupError):
                                session_lookup_error = error
                            else:
                                raise exc

                            if session_lookup_error.is_incorrect_offset():
                                # Reset position in file.
                                offset = (
                                    session_lookup_error.get_incorrect_offset().correct_offset
                                )
                                f.seek(offset)
                                cursor.offset = f.tell()
                            else:
                                raise exc

    def remove(self, dbx_path: str, **kwargs) -> files.Metadata:
        """
        Removes a file / folder from Dropbox.

        :param dbx_path: Path to file on Dropbox.
        :param kwargs: Keyword arguments for the Dropbox API files_delete_v2 endpoint.
        :returns: Metadata of deleted item.
        """

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_delete_v2(dbx_path, **kwargs)
            return res.metadata

    def remove_batch(
        self, entries: List[Tuple[str, str]], batch_size: int = 900
    ) -> List[Union[files.Metadata, MaestralApiError]]:
        """
        Deletes multiple items on Dropbox in a batch job.

        :param entries: List of Dropbox paths and "rev"s to delete. If a "rev" is not
            None, the file will only be deleted if it matches the rev on Dropbox. This
            is not supported when deleting a folder.
        :param batch_size: Number of items to delete in each batch. Dropbox allows
            batches of up to 1,000 items. Larger values will be capped automatically.
        :returns: List of Metadata for deleted items or SyncErrors for failures. Results
            will be in the same order as the original input.
        """

        batch_size = clamp(batch_size, 1, 1000)

        res_entries = []
        result_list = []

        # Up two ~ 1,000 entries allowed per batch:
        # https://www.dropbox.com/developers/reference/data-ingress-guide
        for chunk in chunks(entries, n=batch_size):

            arg = [files.DeleteArg(e[0], e[1]) for e in chunk]

            with convert_api_errors():
                res = self.dbx.files_delete_batch(arg)

            if res.is_complete():
                batch_res = res.get_complete()
                res_entries.extend(batch_res.entries)

            elif res.is_async_job_id():
                async_job_id = res.get_async_job_id()

                time.sleep(1.0)

                with convert_api_errors():
                    res = self.dbx.files_delete_batch_check(async_job_id)

                check_interval = round(len(chunk) / 100, 1)

                while res.is_in_progress():
                    time.sleep(check_interval)
                    with convert_api_errors():
                        res = self.dbx.files_delete_batch_check(async_job_id)

                if res.is_complete():
                    batch_res = res.get_complete()
                    res_entries.extend(batch_res.entries)

                elif res.is_failed():
                    error = res.get_failed()
                    if error.is_too_many_write_operations():
                        title = "Could not delete items"
                        text = (
                            "There are too many write operations happening in your "
                            "Dropbox. Please try again later."
                        )
                        raise SyncError(title, text)

        for i, entry in enumerate(res_entries):
            if entry.is_success():
                result_list.append(entry.get_success().metadata)
            elif entry.is_failure():
                exc = exceptions.ApiError(
                    error=entry.get_failure(),
                    user_message_text="",
                    user_message_locale="",
                    request_id="",
                )
                sync_err = dropbox_to_maestral_error(exc, dbx_path=entries[i][0])
                result_list.append(sync_err)

        return result_list

    def move(self, dbx_path: str, new_path: str, **kwargs) -> files.Metadata:
        """
        Moves / renames files or folders on Dropbox.

        :param dbx_path: Path to file/folder on Dropbox.
        :param new_path: New path on Dropbox to move to.
        :param kwargs: Keyword arguments for the Dropbox API files_move_v2 endpoint.
        :returns: Metadata of moved item.
        """

        with convert_api_errors(dbx_path=new_path):
            res = self.dbx.files_move_v2(
                dbx_path,
                new_path,
                allow_shared_folder=True,
                allow_ownership_transfer=True,
                **kwargs,
            )
            return res.metadata

    def make_dir(self, dbx_path: str, **kwargs) -> files.FolderMetadata:
        """
        Creates a folder on Dropbox.

        :param dbx_path: Path of Dropbox folder.
        :param kwargs: Keyword arguments for the Dropbox API files_create_folder_v2
            endpoint.
        :returns: Metadata of created folder.
        """

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_create_folder_v2(dbx_path, **kwargs)
            return res.metadata

    def make_dir_batch(
        self, dbx_paths: List[str], batch_size: int = 900, **kwargs
    ) -> List[Union[files.Metadata, MaestralApiError]]:
        """
        Creates multiple folders on Dropbox in a batch job.

        :param dbx_paths: List of dropbox folder paths.
        :param batch_size: Number of folders to create in each batch. Dropbox allows
            batches of up to 1,000 folders. Larger values will be capped automatically.
        :param kwargs: Keyword arguments for the Dropbox API files/create_folder_batch
            endpoint.
        :returns: List of Metadata for created folders or SyncError for failures.
            Entries will be in the same order as given paths.
        """
        batch_size = clamp(batch_size, 1, 1000)

        entries = []
        result_list = []

        with convert_api_errors():

            # Up two ~ 1,000 entries allowed per batch:
            # https://www.dropbox.com/developers/reference/data-ingress-guide
            for chunk in chunks(dbx_paths, n=batch_size):
                res = self.dbx.files_create_folder_batch(chunk, **kwargs)
                if res.is_complete():
                    batch_res = res.get_complete()
                    entries.extend(batch_res.entries)
                elif res.is_async_job_id():
                    async_job_id = res.get_async_job_id()

                    time.sleep(1.0)
                    res = self.dbx.files_create_folder_batch_check(async_job_id)

                    check_interval = round(len(chunk) / 100, 1)

                    while res.is_in_progress():
                        time.sleep(check_interval)
                        res = self.dbx.files_create_folder_batch_check(async_job_id)

                    if res.is_complete():
                        batch_res = res.get_complete()
                        entries.extend(batch_res.entries)

                    elif res.is_failed():
                        error = res.get_failed()
                        if error.is_too_many_files():
                            res_list = self.make_dir_batch(
                                chunk, batch_size=round(batch_size / 2), **kwargs
                            )
                            result_list.extend(res_list)

        for i, entry in enumerate(entries):
            if entry.is_success():
                result_list.append(entry.get_success().metadata)
            elif entry.is_failure():
                exc = exceptions.ApiError(
                    error=entry.get_failure(),
                    user_message_text="",
                    user_message_locale="",
                    request_id="",
                )
                sync_err = dropbox_to_maestral_error(exc, dbx_path=dbx_paths[i])
                result_list.append(sync_err)

        return result_list

    def get_latest_cursor(
        self, dbx_path: str, include_non_downloadable_files: bool = False, **kwargs
    ) -> str:
        """
        Gets the latest cursor for the given folder and subfolders.

        :param dbx_path: Path of folder on Dropbox.
        :param include_non_downloadable_files: If ``True``, files that cannot be
            downloaded (at the moment only G-suite files on Dropbox) will be included.
        :param kwargs: Additional keyword arguments for Dropbox API
            files/list_folder/get_latest_cursor endpoint.
        :returns: The latest cursor representing a state of a folder and its subfolders.
        """

        dbx_path = "" if dbx_path == "/" else dbx_path

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_list_folder_get_latest_cursor(
                dbx_path,
                include_non_downloadable_files=include_non_downloadable_files,
                recursive=True,
                **kwargs,
            )

        return res.cursor

    def list_folder(
        self,
        dbx_path: str,
        max_retries_on_timeout: int = 4,
        include_non_downloadable_files: bool = False,
        **kwargs,
    ) -> files.ListFolderResult:
        """
        Lists the contents of a folder on Dropbox. Similar to
        :meth:`list_folder_iterator` but returns all entries in a single
        :class:`dropbox.files.ListFolderResult` instance.

        :param dbx_path: Path of folder on Dropbox.
        :param max_retries_on_timeout: Number of times to try again if Dropbox servers
            do not respond within the timeout. Occasional timeouts may occur for very
            large Dropbox folders.
        :param include_non_downloadable_files: If ``True``, files that cannot be
            downloaded (at the moment only G-suite files on Dropbox) will be included.
        :param kwargs: Additional keyword arguments for Dropbox API files/list_folder
            endpoint.
        :returns: Content of given folder.
        """

        iterator = self.list_folder_iterator(
            dbx_path,
            max_retries_on_timeout,
            include_non_downloadable_files,
            **kwargs,
        )

        return self.flatten_results(list(iterator), attribute_name="entries")

    def list_folder_iterator(
        self,
        dbx_path: str,
        max_retries_on_timeout: int = 4,
        include_non_downloadable_files: bool = False,
        **kwargs,
    ) -> Iterator[files.ListFolderResult]:
        """
        Lists the contents of a folder on Dropbox. Returns an iterator yielding
        :class:`dropbox.files.ListFolderResult` instances. The number of entries
        returned in each iteration corresponds to the number of entries returned by a
        single Dropbox API call and will be typically around 500.

        :param dbx_path: Path of folder on Dropbox.
        :param max_retries_on_timeout: Number of times to try again if Dropbox servers
            do not respond within the timeout. Occasional timeouts may occur for very
            large Dropbox folders.
        :param include_non_downloadable_files: If ``True``, files that cannot be
            downloaded (at the moment only G-suite files on Dropbox) will be included.
        :param kwargs: Additional keyword arguments for the Dropbox API
            files/list_folder endpoint.
        :returns: Iterator over content of given folder.
        """

        with convert_api_errors(dbx_path):

            dbx_path = "" if dbx_path == "/" else dbx_path

            res = self.dbx.files_list_folder(
                dbx_path,
                include_non_downloadable_files=include_non_downloadable_files,
                **kwargs,
            )

            yield res

            while res.has_more:

                attempt = 0

                while True:
                    try:
                        res = self.dbx.files_list_folder_continue(res.cursor)
                        yield res
                        break
                    except requests.exceptions.ReadTimeout:
                        attempt += 1
                        if attempt <= max_retries_on_timeout:
                            time.sleep(5.0)
                        else:
                            raise

    def wait_for_remote_changes(self, last_cursor: str, timeout: int = 40) -> bool:
        """
        Waits for remote changes since ``last_cursor``. Call this method after
        starting the Dropbox client and periodically to get the latest updates.

        :param last_cursor: Last to cursor to compare for changes.
        :param timeout: Seconds to wait until timeout. Must be between 30 and 480. The
            Dropbox API will add a random jitter of up to 60 sec to this value.
        :returns: ``True`` if changes are available, ``False`` otherwise.
        """

        if not 30 <= timeout <= 480:
            raise ValueError("Timeout must be in range [30, 480]")

        # Honour last request to back off.
        time_to_backoff = max(self._backoff_until - time.time(), 0)
        time.sleep(time_to_backoff)

        with convert_api_errors():
            res = self.dbx.files_list_folder_longpoll(last_cursor, timeout=timeout)

        # Keep track of last longpoll, back off if requested by API.
        if res.backoff:
            self._logger.debug("Backoff requested for %s sec", res.backoff)
            self._backoff_until = time.time() + res.backoff + 5.0
        else:
            self._backoff_until = 0

        return res.changes

    def list_remote_changes(self, last_cursor: str) -> files.ListFolderResult:
        """
        Lists changes to remote Dropbox since ``last_cursor``. Same as
        :meth:`list_remote_changes_iterator` but fetches all changes first and returns
        a single :class:`dropbox.files.ListFolderResult`. This may be useful if you want
        to fetch all changes in advance before starting to process them.

        :param last_cursor: Last to cursor to compare for changes.
        :returns: Remote changes since given cursor.
        """

        iterator = self.list_remote_changes_iterator(last_cursor)
        return self.flatten_results(list(iterator), attribute_name="entries")

    def list_remote_changes_iterator(
        self, last_cursor: str
    ) -> Iterator[files.ListFolderResult]:
        """
        Lists changes to the remote Dropbox since ``last_cursor``. Returns an iterator
        yielding :class:`dropbox.files.ListFolderResult` instances. The number of
        entries returned in each iteration corresponds to the number of entries returned
        by a single Dropbox API call and will be typically around 500.

        Call this after :meth:`wait_for_remote_changes` returns ``True``.

        :param last_cursor: Last to cursor to compare for changes.
        :returns: Iterator over remote changes since given cursor.
        """

        with convert_api_errors():

            result = self.dbx.files_list_folder_continue(last_cursor)

            yield result

            while result.has_more:
                result = self.dbx.files_list_folder_continue(result.cursor)
                yield result

    def create_shared_link(
        self,
        dbx_path: str,
        visibility: sharing.RequestedVisibility = sharing.RequestedVisibility.public,
        password: Optional[str] = None,
        expires: Optional[datetime] = None,
        **kwargs,
    ) -> sharing.SharedLinkMetadata:
        """
        Creates a shared link for the given path. Some options are only available for
        Professional and Business accounts. Note that the requested visibility as access
        level for the link may not be granted, depending on the Dropbox folder or team
        settings. Check the returned link metadata to verify the visibility and access
        level.

        :param dbx_path: Dropbox path to file or folder to share.
        :param visibility: The visibility of the shared link. Can be public, team-only,
            or password protected. In case of the latter, the password argument must be
            given. Only available for Professional and Business accounts.
        :param password: Password to protect shared link. Is required if visibility
            is set to password protected and will be ignored otherwise
        :param expires: Expiry time for shared link. Only available for Professional and
            Business accounts.
        :param kwargs: Additional keyword arguments to create the
            :class:`dropbox.sharing.SharedLinkSettings` instance.
        :returns: Metadata for shared link.
        """

        if visibility.is_password() and not password:
            raise MaestralApiError(
                "Invalid shared link setting",
                "Password is required to share a password-protected link",
            )

        if not visibility.is_password():
            password = None

        # Convert timestamp to utc time if not naive.
        if expires is not None:
            has_timezone = expires.tzinfo and expires.tzinfo.utcoffset(expires)
            if has_timezone:
                expires.astimezone(timezone.utc)

        settings = sharing.SharedLinkSettings(
            requested_visibility=visibility,
            link_password=password,
            expires=expires,
            **kwargs,
        )

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.sharing_create_shared_link_with_settings(dbx_path, settings)

        return res

    def revoke_shared_link(self, url: str) -> None:
        """
        Revokes a shared link.

        :param url: URL to revoke.
        """
        with convert_api_errors():
            self.dbx.sharing_revoke_shared_link(url)

    def list_shared_links(
        self, dbx_path: Optional[str] = None
    ) -> sharing.ListSharedLinksResult:
        """
        Lists all shared links for a given Dropbox path (file or folder). If no path is
        given, list all shared links for the account, up to a maximum of 1,000 links.

        :param dbx_path: Dropbox path to file or folder.
        :returns: Shared links for a path, including any shared links for parents
            through which this path is accessible.
        """

        results = []

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.sharing_list_shared_links(dbx_path)
            results.append(res)

            while results[-1].has_more:
                res = self.dbx.sharing_list_shared_links(dbx_path, results[-1].cursor)
                results.append(res)

        return self.flatten_results(results, attribute_name="links")

    @staticmethod
    def flatten_results(
        results: List[PaginationResultType], attribute_name: str
    ) -> PaginationResultType:
        """
        Flattens a list of Dropbox API results from a pagination to a single result with
        the cursor of the last result in the list.

        :param results: List of :results to flatten.
        :param attribute_name: Name of attribute to flatten.
        :returns: Flattened result.
        """

        all_entries = []

        for result in results:
            all_entries += getattr(result, attribute_name)

        kwargs = {
            attribute_name: all_entries,
            "cursor": results[-1].cursor,
            "has_more": False,
        }

        result_cls = type(results[0])
        results_flattened = result_cls(**kwargs)

        return results_flattened


# ==== conversion functions to generate error messages and types =======================


def os_to_maestral_error(
    exc: OSError, dbx_path: Optional[str] = None, local_path: Optional[str] = None
) -> LocalError:
    """
    Converts a :class:`OSError` to a :class:`maestral.errors.MaestralApiError` and tries
    to add a reasonably informative error title and message.

    :param exc: Original OSError.
    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    :returns: Converted exception.
    """

    title = "Could not sync file or folder"
    err_cls: Type[MaestralApiError]

    if isinstance(exc, PermissionError):
        err_cls = InsufficientPermissionsError  # subclass of SyncError
        text = "Insufficient read or write permissions for this location."
    elif isinstance(exc, FileNotFoundError):
        err_cls = NotFoundError  # subclass of SyncError
        text = "The given path does not exist."
    elif isinstance(exc, FileExistsError):
        err_cls = ConflictError  # subclass of SyncError
        title = "Could not download file"
        text = "There already is an item at the given path."
    elif isinstance(exc, IsADirectoryError):
        err_cls = IsAFolderError  # subclass of SyncError
        title = "Could not create local file"
        text = "The given path refers to a folder."
    elif isinstance(exc, NotADirectoryError):
        err_cls = NotAFolderError  # subclass of SyncError
        title = "Could not create local folder"
        text = "The given path refers to a file."
    elif exc.errno == errno.ENAMETOOLONG:
        err_cls = PathError  # subclass of SyncError
        title = "Could not create local file"
        text = "The file name (including path) is too long."
    elif exc.errno == errno.EINVAL:
        err_cls = PathError  # subclass of SyncError
        title = "Could not create local file"
        text = (
            "The file name contains characters which are not allowed on your file "
            "system. This could be for instance a colon or a trailing period."
        )
    elif exc.errno == errno.EFBIG:
        err_cls = FileSizeError  # subclass of SyncError
        title = "Could not download file"
        text = "The file size too large."
    elif exc.errno == errno.ENOSPC:
        err_cls = InsufficientSpaceError  # subclass of SyncError
        title = "Could not download file"
        text = "There is not enough space left on the selected drive."
    elif exc.errno == errno.EFAULT:
        err_cls = FileReadError  # subclass of SyncError
        title = "Could not upload file"
        text = "An error occurred while reading the file content."
    elif exc.errno == errno.ENOMEM:
        err_cls = OutOfMemoryError  # subclass of MaestralApiError
        text = "Out of memory. Please reduce the number of memory consuming processes."
    else:
        return exc

    local_path = local_path or exc.filename

    maestral_exc = err_cls(title, text, dbx_path=dbx_path, local_path=local_path)
    maestral_exc.__cause__ = exc

    return maestral_exc


def dropbox_to_maestral_error(
    exc: Union[exceptions.DropboxException, ValidationError, requests.HTTPError],
    dbx_path: Optional[str] = None,
    local_path: Optional[str] = None,
) -> MaestralApiError:
    """
    Converts a Dropbox SDK exception to a :class:`maestral.errors.MaestralApiError` and
    tries to add a reasonably informative error title and message.

    :param exc: Dropbox SDK exception..
    :param dbx_path: Dropbox path associated with the error.
    :param local_path: Local path associated with the error.
    :returns: Converted exception.
    """

    title = "An unexpected error occurred"
    text = "Please contact the developer with the traceback information from the logs."
    err_cls = MaestralApiError

    # ---- Dropbox API Errors ----------------------------------------------------------
    if isinstance(exc, exceptions.ApiError):

        error = exc.error

        if isinstance(error, files.RelocationError):
            title = "Could not move file or folder"
            if error.is_cant_copy_shared_folder():
                text = "Shared folders can’t be copied."
                err_cls = SyncError
            elif error.is_cant_move_folder_into_itself():
                text = "You cannot move a folder into itself."
                err_cls = ConflictError
            elif error.is_cant_move_shared_folder():
                text = "You cannot move the shared folder to the given destination."
                err_cls = PathError
            elif error.is_cant_nest_shared_folder():
                text = (
                    "Your move operation would result in nested shared folders. "
                    "This is not allowed."
                )
                err_cls = PathError
            elif error.is_cant_transfer_ownership():
                text = (
                    "Your move operation would result in an ownership transfer. "
                    "Maestral does not currently support this. Please carry out "
                    "the move on the Dropbox website instead."
                )
                err_cls = PathError
            elif error.is_duplicated_or_nested_paths():
                text = (
                    "There are duplicated/nested paths among the target and "
                    "destination folders."
                )
                err_cls = PathError
            elif error.is_from_lookup():
                lookup_error = error.get_from_lookup()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_from_write():
                write_error = error.get_from_write()
                text, err_cls = _get_write_error_msg(write_error)
            elif error.is_insufficient_quota():
                text = (
                    "You do not have enough space on Dropbox to move "
                    "or copy the files."
                )
                err_cls = InsufficientSpaceError
            elif error.is_internal_error():
                text = "Something went on Dropbox’s end. Please try again later."
                err_cls = DropboxServerError
            elif error.is_to():
                to_error = error.get_to()
                text, err_cls = _get_write_error_msg(to_error)
            elif error.is_too_many_files():
                text = (
                    "There are more than 10,000 files and folders in one "
                    "request. Please try to move fewer items at once."
                )
                err_cls = SyncError

        elif isinstance(error, (files.CreateFolderError, files.CreateFolderEntryError)):
            title = "Could not create folder"
            if error.is_path():
                write_error = error.get_path()
                text, err_cls = _get_write_error_msg(write_error)

        elif isinstance(error, files.DeleteError):
            title = "Could not delete item"
            if error.is_path_lookup():
                lookup_error = error.get_path_lookup()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_path_write():
                write_error = error.get_path_write()
                text, err_cls = _get_write_error_msg(write_error)
            elif error.is_too_many_files():
                text = (
                    "There are more than 10,000 files and folders in one "
                    "request. Please try to delete fewer items at once."
                )
                err_cls = SyncError
            elif error.is_too_many_write_operations():
                text = (
                    "There are too many write operations happening in your "
                    "Dropbox. Please try again later."
                )
                err_cls = SyncError

        elif isinstance(error, files.UploadError):
            title = "Could not upload file"
            if error.is_path():
                write_error = error.get_path().reason  # returns UploadWriteFailed
                text, err_cls = _get_write_error_msg(write_error)
            elif error.is_properties_error():
                # Occurs only for programming error in maestral.
                text = "Invalid property group provided."

        elif isinstance(error, files.UploadSessionStartError):
            title = "Could not upload file"
            if error.is_concurrent_session_close_not_allowed():
                # Occurs only for programming error in maestral.
                text = "Can not start a closed concurrent upload session."
            elif error.is_concurrent_session_data_not_allowed():
                # Occurs only for programming error in maestral.
                text = "Uploading data not allowed when starting concurrent upload session."

        elif isinstance(error, files.UploadSessionFinishError):
            title = "Could not upload file"
            if error.is_lookup_failed():
                session_lookup_error = error.get_lookup_failed()
                text, err_cls = _get_session_lookup_error_msg(session_lookup_error)
            elif error.is_path():
                write_error = error.get_path()
                text, err_cls = _get_write_error_msg(write_error)
            elif error.is_properties_error():
                # Occurs only for programming error in maestral.
                text = "Invalid property group provided."
            elif error.is_too_many_write_operations():
                text = (
                    "There are too many write operations happening in your "
                    "Dropbox. Please retry again later."
                )
                err_cls = SyncError

        elif isinstance(error, files.UploadSessionLookupError):
            title = "Could not upload file"
            text, err_cls = _get_session_lookup_error_msg(error)

        elif isinstance(error, files.DownloadError):
            title = "Could not download file"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_unsupported_file():
                text = "This file type cannot be downloaded but must be exported."
                err_cls = UnsupportedFileError

        elif isinstance(error, files.ListFolderError):
            title = "Could not list folder contents"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)

        elif isinstance(error, files.ListFolderContinueError):
            title = "Could not list folder contents"
            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_reset():
                text = (
                    "Dropbox has reset its sync state. Please rebuild "
                    "Maestral's index to re-sync your Dropbox."
                )
                err_cls = CursorResetError

        elif isinstance(error, files.ListFolderLongpollError):
            title = "Could not get Dropbox changes"
            if error.is_reset():
                text = (
                    "Dropbox has reset its sync state. Please rebuild "
                    "Maestral's index to re-sync your Dropbox."
                )
                err_cls = CursorResetError

        elif isinstance(error, async_.PollError):

            title = "Could not get status of batch job"

            if error.is_internal_error():
                text = (
                    "Something went wrong with the job on Dropbox’s end. Please "
                    "verify on the Dropbox website if the job succeeded and try "
                    "again if it failed."
                )
                err_cls = DropboxServerError
            else:
                # Other tags include invalid_async_job_id.
                # Neither should occur in our SDK usage.
                pass

        elif isinstance(error, files.ListRevisionsError):

            title = "Could not list file revisions"

            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)

        elif isinstance(error, files.RestoreError):

            title = "Could not restore file"

            if error.is_invalid_revision():
                text = "Invalid revision."
                err_cls = NotFoundError
            elif error.is_path_lookup():
                lookup_error = error.get_path_lookup()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_path_write():
                write_error = error.get_path_write()
                text, err_cls = _get_write_error_msg(write_error)
            elif error.is_in_progress():
                title = "Restore in progress"
                text = "Please check again later if the restore completed"
                err_cls = SyncError

        elif isinstance(error, files.GetMetadataError):
            title = "Could not get metadata"

            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)

        elif isinstance(error, users.GetAccountError):
            title = "Could not get account info"

            if error.is_no_account():
                text = (
                    "An account with the given Dropbox ID does not "
                    "exist or has been deleted"
                )
                err_cls = InvalidDbidError

        elif isinstance(error, sharing.CreateSharedLinkWithSettingsError):
            title = "Could not create shared link"

            if error.is_access_denied():
                text = "You do not have access to create shared links for this path."
                err_cls = InsufficientPermissionsError
            elif error.is_email_not_verified():
                text = "Please verify you email address before creating shared links"
                err_cls = SharedLinkError
            elif error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_settings_error():
                settings_error = error.get_settings_error()
                err_cls = SharedLinkError
                if settings_error.is_invalid_settings():
                    text = "Please check if the settings are valid."
                elif settings_error.is_not_authorized():
                    text = "Basic accounts do not support passwords or expiry dates."
            elif error.is_shared_link_already_exists():
                text = "The shared link already exists."
                err_cls = SharedLinkError

        elif isinstance(error, sharing.RevokeSharedLinkError):
            title = "Could not revoke shared link"

            if error.is_shared_link_malformed():
                text = "The shared link is malformed."
                err_cls = SharedLinkError

            elif error.is_shared_link_not_found():
                text = "The given link does not exist."
                err_cls = NotFoundError
            elif error.is_shared_link_access_denied():
                text = "You do not have access to revoke the shared link."
                err_cls = InsufficientPermissionsError
            elif error.is_unsupported_link_type():
                text = "The link type is not supported."
                err_cls = SharedLinkError

        elif isinstance(error, sharing.ListSharedLinksError):
            title = "Could not list shared links"

            if error.is_path():
                lookup_error = error.get_path()
                text, err_cls = _get_lookup_error_msg(lookup_error)
            elif error.is_reset():
                text = "Please try again later."
                err_cls = SharedLinkError

    # ---- Authentication errors -------------------------------------------------------
    elif isinstance(exc, exceptions.AuthError):
        error = exc.error
        if isinstance(error, auth.AuthError):
            if error.is_expired_access_token():
                err_cls = TokenExpiredError
                title = "Authentication error"
                text = (
                    "Maestral's access to your Dropbox has expired. Please relink "
                    "to continue syncing."
                )
            elif error.is_invalid_access_token():
                err_cls = TokenRevokedError
                title = "Authentication error"
                text = (
                    "Maestral's access to your Dropbox has been revoked. Please "
                    "relink to continue syncing."
                )
            elif error.is_user_suspended():
                err_cls = DropboxAuthError
                title = "Authentication error"
                text = "Your user account has been suspended."
            else:
                # Other tags are invalid_select_admin, invalid_select_user,
                # missing_scope, route_access_denied. Neither should occur in our SDK
                # usage.
                pass

        else:
            err_cls = DropboxAuthError
            title = "Authentication error"
            text = "Please check if you can log in on the Dropbox website."

    # ---- OAuth2 flow errors ----------------------------------------------------------
    elif isinstance(exc, requests.HTTPError):
        # HTTPErrors are converted to a DropboxException by the SDK unless they occur
        # when refreshing the access token. We therefore handle those manually.
        # See https://github.com/dropbox/dropbox-sdk-python/issues/360
        # and https://github.com/SamSchott/maestral/issues/388.

        if exc.request is not None and exc.request.status_code >= 500:
            err_cls = DropboxServerError
            title = "Dropbox server error"
            text = (
                "Something went wrong on Dropbox’s end. Please check on "
                "status.dropbox.com if their services are up and running and try again "
                "later."
            )
        else:
            err_cls = DropboxAuthError
            title = "Authentication failed"
            text = "Please make sure that you entered the correct authentication code."

    elif isinstance(exc, oauth.BadStateException):
        err_cls = DropboxAuthError
        title = "Authentication session expired."
        text = "The authentication session expired. Please try again."

    elif isinstance(exc, oauth.NotApprovedException):
        err_cls = DropboxAuthError
        title = "Not approved error"
        text = "Please grant Maestral access to your Dropbox to start syncing."

    # ---- Bad input errors ------------------------------------------------------------
    # should only occur due to user input from console scripts
    elif isinstance(exc, (exceptions.BadInputError, ValidationError)):
        err_cls = BadInputError
        title = "Bad input to API call"
        text = exc.message

    # ---- Internal Dropbox error ------------------------------------------------------
    elif isinstance(exc, exceptions.InternalServerError):
        err_cls = DropboxServerError
        title = "Dropbox server error"
        text = (
            "Something went wrong on Dropbox’s end. Please check on status.dropbox.com "
            "if their services are up and running and try again later."
        )

    maestral_exc = err_cls(title, text, dbx_path=dbx_path, local_path=local_path)
    maestral_exc.__cause__ = exc

    return maestral_exc


def _get_write_error_msg(write_error: files.WriteError) -> Tuple[str, WriteErrorType]:

    text = ""
    err_cls = SyncError

    if write_error.is_conflict():
        conflict = write_error.get_conflict()
        if conflict.is_file():
            text = (
                "Could not write to the target path because another file "
                "was in the way."
            )
            err_cls = FileConflictError
        elif conflict.is_folder():
            text = (
                "Could not write to the target path because another folder "
                "was in the way."
            )
            err_cls = FolderConflictError
        elif conflict.is_file_ancestor():
            text = (
                "Could not create parent folders because another file "
                "was in the way."
            )
            err_cls = FileConflictError
        else:
            text = (
                "Could not write to the target path because another file or "
                "folder was in the way."
            )
            err_cls = ConflictError
    elif write_error.is_disallowed_name():
        text = "Dropbox will not save the file or folder because of its name."
        err_cls = PathError
    elif write_error.is_insufficient_space():
        text = "You do not have enough space on Dropbox to move or copy the files."
        err_cls = InsufficientSpaceError
    elif write_error.is_malformed_path():
        text = (
            "The destination path is invalid. Paths may not end with a slash or "
            "whitespace."
        )
        err_cls = PathError
    elif write_error.is_no_write_permission():
        text = "You do not have permissions to write to the target location."
        err_cls = InsufficientPermissionsError
    elif write_error.is_team_folder():
        text = "You cannot move or delete team folders through Maestral."
    elif write_error.is_too_many_write_operations():
        text = (
            "There are too many write operations in your Dropbox. Please "
            "try again later."
        )

    return text, err_cls


def _get_lookup_error_msg(
    lookup_error: files.LookupError,
) -> Tuple[str, LookupErrorType]:

    text = ""
    err_cls = SyncError

    if lookup_error.is_malformed_path():
        text = "The path is invalid. Paths may not end with a slash or whitespace."
        err_cls = PathError
    elif lookup_error.is_not_file():
        text = "The given path refers to a folder."
        err_cls = IsAFolderError
    elif lookup_error.is_not_folder():
        text = "The given path refers to a file."
        err_cls = NotAFolderError
    elif lookup_error.is_not_found():
        text = "There is nothing at the given path."
        err_cls = NotFoundError
    elif lookup_error.is_restricted_content():
        text = (
            "The file cannot be transferred because the content is restricted. For "
            "example, sometimes there are legal restrictions due to copyright "
            "claims."
        )
        err_cls = RestrictedContentError
    elif lookup_error.is_unsupported_content_type():
        text = "This file type is currently not supported for syncing."
        err_cls = UnsupportedFileError
    elif lookup_error.is_locked():
        text = "The given path is locked."
        err_cls = InsufficientPermissionsError

    return text, err_cls


def _get_session_lookup_error_msg(
    session_lookup_error: files.UploadSessionLookupError,
) -> Tuple[str, SessionLookupErrorType]:

    text = ""
    err_cls = SyncError

    if session_lookup_error.is_closed():
        # Occurs when trying to append data to a closed session.
        # This is caused by internal Maestral errors.
        pass
    elif session_lookup_error.is_incorrect_offset():
        text = "A network error occurred during the upload session."
    elif session_lookup_error.is_not_closed():
        # Occurs when trying to finish an open session.
        # This is caused by internal Maestral errors.
        pass
    elif session_lookup_error.is_not_found():
        text = (
            "The upload session ID was not found or has expired. "
            "Upload sessions are valid for 48 hours."
        )
    elif session_lookup_error.is_too_large():
        text = "You can only upload files up to 350 GB."
        err_cls = FileSizeError

    return text, err_cls
