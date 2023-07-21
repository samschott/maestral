"""
This module contains the Dropbox API client. It wraps calls to the Dropbox Python SDK
and handles exceptions, chunked uploads or downloads, etc.
"""

from __future__ import annotations

# system imports
import os
import re
import time
import functools
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from typing import (
    Callable,
    Iterator,
    Sequence,
    TypeVar,
    Any,
    BinaryIO,
    overload,
    cast,
    TYPE_CHECKING,
)
from typing_extensions import ParamSpec, Concatenate

# external imports
import requests
from dropbox import files, sharing, users, common
from dropbox import Dropbox, create_session, exceptions
from dropbox.oauth import DropboxOAuth2FlowNoRedirect
from dropbox.session import API_HOST
from dropbox.dropbox_client import (
    BadInputException,
    RouteResult,
    RouteErrorResult,
    USER_AUTH,
)

# local imports
from . import __version__
from .keyring import CredentialStorage
from .logging import scoped_logger
from .core import (
    AccountType,
    Team,
    Account,
    RootInfo,
    UserRootInfo,
    TeamRootInfo,
    FullAccount,
    TeamSpaceUsage,
    SpaceUsage,
    WriteMode,
    Metadata,
    DeletedMetadata,
    FileMetadata,
    FolderMetadata,
    ListFolderResult,
    LinkAccessLevel,
    LinkAudience,
    LinkPermissions,
    SharedLinkMetadata,
    ListSharedLinkResult,
)
from .exceptions import (
    MaestralApiError,
    SyncError,
    PathError,
    NotFoundError,
    NotLinkedError,
    DataCorruptionError,
    DataChangedError,
)
from .errorhandling import (
    convert_api_errors,
    dropbox_to_maestral_error,
    CONNECTION_ERRORS,
)
from .config import MaestralState
from .constants import DROPBOX_APP_KEY
from .utils import natural_size, chunks, clamp
from .utils.path import opener_no_symlink, delete
from .utils.hashing import DropboxContentHasher, StreamHasher

if TYPE_CHECKING:
    from .models import SyncEvent


__all__ = ["DropboxClient", "API_HOST"]


PRT = TypeVar("PRT", ListFolderResult, ListSharedLinkResult)
P = ParamSpec("P")
T = TypeVar("T")

USER_AGENT = f"Maestral/v{__version__}"


def get_hash(data: bytes) -> str:
    hasher = DropboxContentHasher()
    hasher.update(data)
    return hasher.hexdigest()


class _DropboxSDK(Dropbox):
    def request_json_string(
        self,
        host: str,
        func_name: str,
        route_style: str,
        request_json_arg: bytes,
        auth_type: str,
        request_binary: bytes | Iterator[bytes] | None,
        timeout: float | None = None,
    ) -> RouteResult | RouteErrorResult:
        # Custom handling to allow for streamed and chunked uploads. This is mostly
        # reproduced from the parent function but without limiting the request body
        # to bytes only.
        if route_style == self._ROUTE_STYLE_UPLOAD:
            fq_hostname = self._host_map[host]
            url = self._get_route_url(fq_hostname, func_name)

            auth_types = auth_type.replace(" ", "").split(",")

            if USER_AUTH not in auth_types:
                raise BadInputException("Unhandled auth type: {}".format(auth_type))

            headers = {
                "User-Agent": self._user_agent,
                "Authorization": f"Bearer {self._oauth2_access_token}",
            }

            if self._headers:
                headers.update(self._headers)

            headers["Content-Type"] = "application/octet-stream"
            headers["Dropbox-API-Arg"] = request_json_arg
            body = request_binary

            if timeout is None:
                timeout = self._timeout

            r = self._session.post(
                url,
                headers=headers,
                data=body,
                stream=False,
                verify=True,
                timeout=timeout,
            )

            self.raise_dropbox_error_for_resp(r)

            if r.status_code in (403, 404, 409):
                raw_resp = r.content.decode("utf-8")
                request_id = r.headers.get("x-dropbox-request-id")
                return RouteErrorResult(request_id, raw_resp)

            raw_resp = r.content.decode("utf-8")
            return RouteResult(raw_resp)

        else:
            return super().request_json_string(
                host,
                func_name,
                route_style,
                request_json_arg,
                auth_type,
                request_binary,
                timeout,
            )


class DropboxClient:
    """Client for the Dropbox SDK

    This client defines basic methods to wrap Dropbox Python SDK calls, such as
    creating, moving, modifying and deleting files and folders on Dropbox and
    downloading files from Dropbox.

    All Dropbox SDK exceptions, OSErrors from the local file system API and connection
    errors will be caught and reraised as a subclass of
    :exc:`maestral.exceptions.MaestralApiError`.

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
    :param bandwidth_limit_up: Maximum bandwidth to use for uploads in bytes/sec. Will
        be enforced over all concurrent uploads (0 = unlimited).
    :param bandwidth_limit_down: Maximum bandwidth to use for downloads in bytes/sec.
        Will be enforced over all concurrent downloads (0 = unlimited).
    """

    SDK_VERSION: str = "2.0"

    MAX_TRANSFER_RETRIES = 10
    MAX_LIST_FOLDER_RETRIES = 3

    UPLOAD_REQUEST_CHUNK_SIZE = 4194304
    DATA_TRANSFER_MIN_SLEEP_TIME = 0.0001  # 100 nanoseconds

    _dbx: _DropboxSDK | None

    def __init__(
        self,
        config_name: str,
        cred_storage: CredentialStorage,
        timeout: float = 100,
        session: requests.Session | None = None,
        bandwidth_limit_up: float = 0,
        bandwidth_limit_down: float = 0,
    ) -> None:
        self.config_name = config_name
        self._auth_flow: DropboxOAuth2FlowNoRedirect | None = None
        self._cred_storage = cred_storage

        self._state = MaestralState(config_name)
        self._logger = scoped_logger(__name__, self.config_name)
        self._dropbox_sdk_logger = scoped_logger("maestral.dropbox", self.config_name)
        self._dropbox_sdk_logger.info = self._dropbox_sdk_logger.debug  # type: ignore

        self._timeout = timeout
        self._session = session or create_session()
        self._backoff_until = 0
        self._dbx: _DropboxSDK | None = None
        self._dbx_base: _DropboxSDK | None = None
        self._cached_account_info: FullAccount | None = None
        self._namespace_id = self._state.get("account", "path_root_nsid")
        self._is_team_space = self._state.get("account", "path_root_type") == "team"

        # Throttling infra
        self.bandwidth_limit_up = bandwidth_limit_up
        self.bandwidth_limit_down = bandwidth_limit_down

        self.download_chunk_size = 4096  # 4 kB
        self.upload_chunk_size = 4096  # 4 kB

        self._num_downloads = 0
        self._num_uploads = 0

    @contextmanager
    def _register_download(self) -> Iterator[None]:
        self._num_downloads += 1
        try:
            yield
        finally:
            self._num_downloads -= 1

    @contextmanager
    def _register_upload(self) -> Iterator[None]:
        self._num_uploads += 1
        try:
            yield
        finally:
            self._num_uploads -= 1

    def _throttled_download_iter(self, iterator: Iterator[T]) -> Iterator[T]:
        for i in iterator:
            if self.bandwidth_limit_down == 0:
                yield i
            else:
                tick = time.monotonic()
                yield i
                tock = time.monotonic()

                speed_per_download = self.bandwidth_limit_down / self._num_downloads
                target_tock = tick + self.download_chunk_size / speed_per_download

                wait_time = target_tock - tock
                if wait_time > self.DATA_TRANSFER_MIN_SLEEP_TIME:
                    time.sleep(wait_time)

    def _throttled_upload_iter(self, data: bytes) -> Iterator[bytes] | bytes:
        pos = 0
        while pos < len(data):
            tick = time.monotonic()
            yield data[pos : pos + self.upload_chunk_size]
            tock = time.monotonic()

            pos += self.upload_chunk_size

            if self.bandwidth_limit_up > 0:
                speed_per_upload = self.bandwidth_limit_up / self._num_uploads
                target_tock = tick + self.upload_chunk_size / speed_per_upload

                wait_time = target_tock - tock
                if wait_time > self.DATA_TRANSFER_MIN_SLEEP_TIME:
                    time.sleep(wait_time)

    def _retry_on_error(  # type: ignore
        error_cls: type[Exception],
        max_retries: int,
        backoff: int = 0,
        msg_regex: str | None = None,
    ) -> Callable[
        [Callable[Concatenate[DropboxClient, P], T]],
        Callable[Concatenate[DropboxClient, P], T],
    ]:
        """
        A decorator to retry a function call if a specified exception occurs.

        :param error_cls: Error type to catch.
        :param max_retries: Maximum number of retries.
        :param msg_regex: If provided, retry errors only if the regex matches the error
            message. Matches are found with :meth:`re.search()`.
        :param backoff: Time in seconds to sleep before retry.
        """

        def decorator(
            func: Callable[Concatenate[DropboxClient, P], T]
        ) -> Callable[Concatenate[DropboxClient, P], T]:
            @functools.wraps(func)
            def wrapper(__self: DropboxClient, *args: P.args, **kwargs: P.kwargs) -> T:
                tries = 0

                while True:
                    try:
                        return func(__self, *args, **kwargs)
                    except error_cls as exc:
                        if msg_regex is not None:
                            # Raise if there is no error message to match.
                            if len(exc.args[0]) == 0 or not isinstance(
                                exc.args[0], str
                            ):
                                raise exc
                            # Raise if regex does not match message.
                            if not re.search(msg_regex, exc.args[0]):
                                raise exc

                        if tries < max_retries:
                            tries += 1
                            if backoff > 0:
                                time.sleep(backoff)
                            __self._logger.debug(
                                "Retrying call %s on %s: %s/%s",
                                func,
                                error_cls,
                                tries,
                                max_retries,
                            )
                        else:
                            raise exc

            return wrapper

        return decorator

    # ---- Linking API -----------------------------------------------------------------

    @property
    def dbx_base(self) -> _DropboxSDK:
        """The underlying Dropbox SDK instance without namespace headers."""
        if not self._dbx_base:
            self._init_sdk()
        return cast(_DropboxSDK, self._dbx_base)

    @property
    def dbx(self) -> _DropboxSDK:
        """The underlying Dropbox SDK instance with namespace headers."""
        if not self._dbx:
            self._init_sdk()
        return cast(_DropboxSDK, self._dbx)

    @property
    def linked(self) -> bool:
        """
        Indicates if the client is linked to a Dropbox account (read only). This will
        block until the user's keyring is unlocked to load the saved auth token.

        :raises KeyringAccessError: if keyring access fails.
        """
        return self._cred_storage.token is not None or self._dbx is not None

    def get_auth_url(self) -> str:
        """
        Returns a URL to authorize access to a Dropbox account. To link a Dropbox
        account, retrieve an authorization code from the URL and link Maestral by
        calling :meth:`link` with the provided code.

        :returns: URL to retrieve an authorization code.
        """
        self._auth_flow = DropboxOAuth2FlowNoRedirect(
            consumer_key=DROPBOX_APP_KEY,
            token_access_type="offline",
            use_pkce=True,
        )
        return self._auth_flow.start()

    def link(
        self,
        code: str | None = None,
        refresh_token: str | None = None,
        access_token: str | None = None,
    ) -> int:
        """
        Links Maestral with a Dropbox account using the given authorization code. The
        code will be exchanged for an access token and a refresh token with Dropbox
        servers. The refresh token will be stored for future usage in the provided
        credential store.

        :param code: Authorization code.
        :param refresh_token: Optionally, instead of an authorization code, directly
            provide a refresh token.
        :param access_token: Optionally, instead of an authorization code or a refresh
            token, directly provide an access token. Note that access tokens are
            short-lived.
        :returns: 0 on success, 1 for an invalid token and 2 for connection errors.
        """
        if code is None and access_token is None and refresh_token is None:
            raise RuntimeError("No auth code, refresh token or access token provided.")

        if code is not None:
            if not self._auth_flow:
                raise RuntimeError("Please start auth flow with 'get_auth_url' first")

            try:
                res = self._auth_flow.finish(code)
            except requests.exceptions.HTTPError:
                return 1
            except CONNECTION_ERRORS:
                return 2

            refresh_token = res.refresh_token

        self._init_sdk(refresh_token=refresh_token, access_token=access_token)

        try:
            account_info = self.get_account_info()
            self.update_path_root(account_info.root_info)
        except CONNECTION_ERRORS:
            return 2

        # Only save long-lived refresh token in storage.
        if refresh_token:
            self._cred_storage.save_creds(account_info.account_id, refresh_token)

        self._auth_flow = None

        return 0

    def unlink(self) -> None:
        """
        Unlinks the Dropbox account. The password will be deleted from the provided
        credential storage.

        :raises KeyringAccessError: if keyring access fails.
        :raises DropboxAuthError: if we cannot authenticate with Dropbox.
        """
        self._dbx = None
        self._dbx_base = None
        self._cached_account_info = None

        with convert_api_errors():
            self.dbx_base.auth_token_revoke()
            self._cred_storage.delete_creds()

    def _init_sdk(
        self,
        refresh_token: str | None = None,
        access_token: str | None = None,
    ) -> None:
        """
        Initialise the SDK. If no token is given, get the token from our credential
        storage.

        :param refresh_token: Long-lived refresh-token for the SDK.
        :param access_token: Short-lived access-token for the SDK.
        :raises RuntimeError: if token is not available from storage and no token is
            passed as an argument.
        """
        refresh_token = refresh_token or self._cred_storage.token
        if refresh_token is None and access_token is None:
            raise NotLinkedError(
                "No auth token set", "Please link a Dropbox account first."
            )

        if refresh_token is not None:
            # Initialise Dropbox SDK.
            self._dbx_base = _DropboxSDK(
                oauth2_refresh_token=refresh_token,
                app_key=DROPBOX_APP_KEY,
                session=self._session,
                user_agent=USER_AGENT,
                timeout=self._timeout,
            )
        else:
            # Initialise Dropbox SDK.
            self._dbx_base = _DropboxSDK(
                oauth2_access_token=access_token,
                app_key=DROPBOX_APP_KEY,
                session=self._session,
                user_agent=USER_AGENT,
                timeout=self._timeout,
            )

        # If namespace_id was given, use the corresponding namespace, otherwise
        # default to the home namespace.
        if self._namespace_id:
            root_path = common.PathRoot.root(self._namespace_id)
            self._dbx = self._dbx_base.with_path_root(root_path)
        else:
            self._dbx = self._dbx_base

        # Set our own logger for the Dropbox SDK.
        self.dbx._logger = self._dropbox_sdk_logger
        self.dbx_base._logger = self._dropbox_sdk_logger

    @property
    def account_info(self) -> FullAccount:
        """Returns cached account info. Use :meth:`get_account_info` to get the latest
        account info from Dropbox servers."""
        if not self._cached_account_info:
            return self.get_account_info()
        else:
            return self._cached_account_info

    @property
    def namespace_id(self) -> str:
        """The namespace ID of the path root currently used by the DropboxClient. All
        file paths will be interpreted as relative to the root namespace. Use
        :meth:`update_path_root` to update the root namespace after the user joins or
        leaves a team with a Team Space."""
        return self._namespace_id

    @property
    def is_team_space(self) -> bool:
        """Whether the user's Dropbox uses a Team Space. Use :meth:`update_path_root` to
        update the root namespace after the user joins or eaves a team with a Team
        Space."""
        return self._is_team_space

    # ---- Session management ----------------------------------------------------------

    def close(self) -> None:
        """Cleans up all resources like the request session/network connection."""
        if self._dbx:
            self._dbx.close()

    def __enter__(self) -> DropboxClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def update_path_root(self, root_info: RootInfo) -> None:
        """
        Updates the root path for the Dropbox client. All files paths given as arguments
        to API calls such as :meth:`list_folder` or :meth:`get_metadata` will be
        interpreted as relative to the root path. All file paths returned by API calls,
        for instance in file metadata, will be relative to this root path.

        The root namespace will change when the user joins or leaves a Dropbox Team with
        Team Spaces. If this happens, API calls using the old root namespace will raise
        a :exc:`maestral.exceptions.PathRootError`. Use this method to update to the new
        root namespace.

        See https://developers.dropbox.com/dbx-team-files-guide and
        https://www.dropbox.com/developers/reference/path-root-header-modes for more
        information on Dropbox Team namespaces and path root headers in API calls.

        .. note:: We don't automatically switch root namespaces because API users may
            want to take action when the path root has changed before making further API
            calls. Be prepared to handle :exc:`maestral.exceptions.PathRootError`
            and act accordingly for all methods.

        :param root_info: :class:`core.RootInfo` describing the path root. Use
            :meth:`get_account_info` to retrieve.
        """
        root_nsid = root_info.root_namespace_id

        path_root = common.PathRoot.root(root_nsid)
        self._dbx = self.dbx_base.with_path_root(path_root)
        self.dbx._logger = self._dropbox_sdk_logger

        if isinstance(root_info, UserRootInfo):
            actual_root_type = "user"
            actual_home_path = ""
        elif isinstance(root_info, TeamRootInfo):
            actual_root_type = "team"
            actual_home_path = root_info.home_path
        else:
            raise MaestralApiError(
                "Unknown root namespace type",
                f"Got {root_info!r} but expected UserRootInfo or TeamRootInfo.",
            )

        self._namespace_id = root_nsid
        self._is_team_space = actual_root_type == "team"

        self._state.set("account", "path_root_nsid", root_nsid)
        self._state.set("account", "path_root_type", actual_root_type)
        self._state.set("account", "home_path", actual_home_path)

        self._logger.debug("Path root type: %s", actual_root_type)
        self._logger.debug("Path root nsid: %s", root_info.root_namespace_id)
        self._logger.debug("User home path: %s", actual_home_path)

    # ---- SDK wrappers ----------------------------------------------------------------

    @overload
    def get_account_info(self, dbid: None = None) -> FullAccount:
        ...

    @overload
    def get_account_info(self, dbid: str) -> Account:
        ...

    def get_account_info(self, dbid: str | None = None) -> Account:
        """
        Gets current account information.

        :param dbid: Dropbox ID of account. If not given, will get the info of the
            currently linked account.
        :returns: Account info.
        """
        with convert_api_errors():
            if dbid:
                res = self.dbx_base.users_get_account(dbid)
                return convert_account(res)

            res = self.dbx_base.users_get_current_account()

            # Save our own account info to config.
            if res.account_type.is_basic():
                account_type = AccountType.Basic
            elif res.account_type.is_business():
                account_type = AccountType.Business
            elif res.account_type.is_pro():
                account_type = AccountType.Pro
            else:
                account_type = AccountType.Other

            self._state.set("account", "email", res.email)
            self._state.set("account", "display_name", res.name.display_name)
            self._state.set("account", "abbreviated_name", res.name.abbreviated_name)
            self._state.set("account", "type", account_type.value)

        if not self._namespace_id:
            home_nsid = res.root_info.home_namespace_id
            self._namespace_id = home_nsid
            self._state.set("account", "path_root_nsid", home_nsid)

        self._cached_account_info = convert_full_account(res)

        return self._cached_account_info

    def get_space_usage(self) -> SpaceUsage:
        """
        :returns: The space usage of the currently linked account.
        """
        with convert_api_errors():
            res = self.dbx_base.users_get_space_usage()

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

        return convert_space_usage(res)

    def get_metadata(
        self, dbx_path: str, include_deleted: bool = False
    ) -> Metadata | None:
        """
        Gets metadata for an item on Dropbox or returns ``False`` if no metadata is
        available. Keyword arguments are passed on to Dropbox SDK files_get_metadata
        call.

        :param dbx_path: Path of folder on Dropbox.
        :param include_deleted: Whether to return data for deleted items.
        :returns: Metadata of item at the given path or ``None`` if item cannot be found.
        """
        try:
            with convert_api_errors(dbx_path=dbx_path):
                res = self.dbx.files_get_metadata(
                    dbx_path, include_deleted=include_deleted
                )
                return convert_metadata(res)
        except (NotFoundError, PathError):
            return None

    def list_revisions(
        self, dbx_path: str, mode: str = "path", limit: int = 10
    ) -> list[FileMetadata]:
        """
        Lists all file revisions for the given file.

        :param dbx_path: Path to file on Dropbox.
        :param mode: Must be 'path' or 'id'. If 'id', specify the Dropbox file ID
            instead of the file path to get revisions across move and rename events.
        :param limit: Maximum number of revisions to list.
        :returns: File revision history.
        """
        with convert_api_errors(dbx_path=dbx_path):
            dbx_mode = files.ListRevisionsMode(mode)
            res = self.dbx.files_list_revisions(dbx_path, mode=dbx_mode, limit=limit)
        return [convert_metadata(entry) for entry in res.entries]

    def restore(self, dbx_path: str, rev: str) -> FileMetadata:
        """
        Restore an old revision of a file.

        :param dbx_path: The path to save the restored file.
        :param rev: The revision to restore. Old revisions can be listed with
            :meth:`list_revisions`.
        :returns: Metadata of restored file.
        """
        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_restore(dbx_path, rev)
        return convert_metadata(res)

    @_retry_on_error(DataCorruptionError, MAX_TRANSFER_RETRIES)
    def download(
        self,
        dbx_path: str,
        local_path: str,
        sync_event: SyncEvent | None = None,
    ) -> FileMetadata:
        """
        Downloads a file from Dropbox to given local path.

        :param dbx_path: Path to file on Dropbox or rev number.
        :param local_path: Path to local download destination.
        :param sync_event: If given, the sync event will be updated with the number of
            downloaded bytes.
        :returns: Metadata of downloaded item.
        :raises DataCorruptionError: if data is corrupted during download.
        """

        with convert_api_errors(dbx_path=dbx_path):
            md, http_resp = self.dbx.files_download(dbx_path)

            with closing(http_resp):
                with open(local_path, "wb", opener=opener_no_symlink) as f:
                    hasher = DropboxContentHasher()
                    wrapped_f = StreamHasher(f, hasher)
                    with self._register_download():
                        for c in self._throttled_download_iter(
                            http_resp.iter_content(self.download_chunk_size)
                        ):
                            wrapped_f.write(c)
                            if sync_event:
                                sync_event.completed = wrapped_f.tell()

                    local_hash = hasher.hexdigest()

                    if md.content_hash != local_hash:
                        delete(local_path)
                        raise DataCorruptionError(
                            "Data corrupted", "Please retry download."
                        )

                    # Dropbox SDK provides naive datetime in UTC.
                    client_mod = md.client_modified.replace(tzinfo=timezone.utc)
                    server_mod = md.server_modified.replace(tzinfo=timezone.utc)

                    # Enforce client_modified < server_modified.
                    now = time.time()
                    mtime = min(client_mod.timestamp(), server_mod.timestamp(), now)
                    # Set mtime of downloaded file.
                    if os.utime in os.supports_fd:
                        os.utime(f.fileno(), (now, mtime))
                    elif os.utime in os.supports_follow_symlinks:
                        os.utime(local_path, (now, mtime), follow_symlinks=False)
                    else:
                        os.utime(local_path, (now, mtime))

        return convert_metadata(md)

    def upload(
        self,
        local_path: str,
        dbx_path: str,
        write_mode: WriteMode = WriteMode.Add,
        update_rev: str | None = None,
        autorename: bool = False,
        sync_event: SyncEvent | None = None,
    ) -> FileMetadata:
        """
        Uploads local file to Dropbox. If the file size is smaller than 4 MB, the file
        will be uploaded all at once. Otherwise, the file will be loaded into memory and
        uploaded in chunks of 4 MB. If the file is modified during this chunked upload,
        this will raise a :exc:`DataChangedError`.

        :param local_path: Path of local file to upload.
        :param dbx_path: Path to save file on Dropbox.
        :param write_mode: Your intent when writing a file to some path. This is used to
            determine what constitutes a conflict and what the autorename strategy is.
            This is used to determine what constitutes a conflict and what the
            autorename strategy is. In some situations, the conflict behavior is
            identical: (a) If the target path doesn't refer to anything, the file is
            always written; no conflict. (b) If the target path refers to a folder, it's
            always a conflict. (c) If the target path refers to a file with identical
            contents, nothing gets written; no conflict. The conflict checking differs
            in the case where there's a file at the target path with contents different
            from the contents you're trying to write.
            :class:`core.WriteMode.Add` Do not overwrite an existing file if there is a
                conflict. The autorename strategy is to append a number to the file
                name. For example, "document.txt" might become "document (2).txt".
            :class:`core.WriteMode.Overwrite` Always overwrite the existing file. The
                autorename strategy is the same as it is for ``add``.
            :class:`core.WriteMode.Update` Overwrite if the given "update_rev" matches
                the existing file's "rev". The supplied value should be the latest known
                "rev" of the file, for example, from :class:`core.FileMetadata`, from
                when the file was last downloaded by the app. This will cause the file
                on the Dropbox servers to be overwritten if the given "rev" matches the
                existing file's current "rev" on the Dropbox servers. The autorename
                strategy is to append the string "conflicted copy" to the file name. For
                example, "document.txt" might become "document (conflicted copy).txt" or
                "document (Panda's conflicted copy).txt".
        :param update_rev: Rev to match for :class:`core.WriteMode.Update`.
        :param sync_event: If given, the sync event will be updated with the number of
            downloaded bytes.
        :param autorename: If there's a conflict, as determined by ``mode``, have the
            Dropbox server try to autorename the file to avoid conflict. The default for
            this field is False.
        :returns: Metadata of uploaded file.
        :raises DataCorruptionError: if data is corrupted during upload.
        :raises DataChangedError: if the file is modified during a chunked upload.
        """
        if write_mode is WriteMode.Add:
            dbx_write_mode = files.WriteMode.add
        elif write_mode is WriteMode.Overwrite:
            dbx_write_mode = files.WriteMode.overwrite
        elif write_mode is WriteMode.Update:
            if update_rev is None:
                raise RuntimeError("Please provide 'update_rev'")
            dbx_write_mode = files.WriteMode.update(update_rev)
        else:
            raise RuntimeError("No write mode for uploading file.")

        with convert_api_errors(dbx_path=dbx_path, local_path=local_path):
            with open(local_path, "rb", opener=opener_no_symlink) as f:
                stat = os.stat(f.fileno())

                with self._register_upload():
                    if stat.st_size <= self.UPLOAD_REQUEST_CHUNK_SIZE:
                        # Upload all at once.
                        res = self._upload_helper(
                            f,
                            dbx_path,
                            dbx_write_mode,
                            autorename,
                            sync_event,
                            stat,
                        )
                    else:
                        # Upload in chunks.
                        # Note: We currently do not support resuming interrupted uploads.
                        # Dropbox keeps upload sessions open for 48h so this could be done
                        # in the future.
                        session_id = self._upload_session_start_helper(
                            f, dbx_path, sync_event, stat
                        )

                        while stat.st_size - f.tell() > self.UPLOAD_REQUEST_CHUNK_SIZE:
                            self._upload_session_append_helper(
                                f, session_id, dbx_path, sync_event, stat
                            )

                        res = self._upload_session_finish_helper(
                            f,
                            session_id,
                            # Commit info.
                            dbx_path,
                            dbx_write_mode,
                            autorename,
                            # Commit info end.
                            sync_event,
                            stat,
                        )

        return convert_metadata(res)

    @_retry_on_error(DataCorruptionError, MAX_TRANSFER_RETRIES)
    def _upload_helper(
        self,
        f: BinaryIO,
        dbx_path: str,
        mode: files.WriteMode,
        autorename: bool,
        sync_event: SyncEvent | None,
        old_stat: os.stat_result,
    ) -> files.FileMetadata:
        data = f.read()
        stat = os.stat(f.fileno())
        if file_was_modified(os.stat(f.fileno()), old_stat):
            raise DataChangedError("File was modified during read")

        try:
            with convert_api_errors(dbx_path=dbx_path):
                md = self.dbx.files_upload(
                    self._throttled_upload_iter(data),
                    dbx_path,
                    client_modified=datetime.utcfromtimestamp(stat.st_mtime),
                    content_hash=get_hash(data),
                    mode=mode,
                    autorename=autorename,
                )
        except Exception:
            # Return to beginning of file.
            f.seek(0)
            raise

        if sync_event:
            sync_event.completed = f.tell()

        return md

    @_retry_on_error(DataCorruptionError, MAX_TRANSFER_RETRIES)
    def _upload_session_start_helper(
        self,
        f: BinaryIO,
        dbx_path: str,
        sync_event: SyncEvent | None,
        old_stat: os.stat_result,
    ) -> str:
        initial_offset = f.tell()
        data = f.read(self.UPLOAD_REQUEST_CHUNK_SIZE)
        if file_was_modified(os.stat(f.fileno()), old_stat):
            raise DataChangedError("File was modified during read")

        try:
            with convert_api_errors(dbx_path=dbx_path):
                session_start = self.dbx.files_upload_session_start(
                    self._throttled_upload_iter(data), content_hash=get_hash(data)
                )
        except Exception:
            # Return to previous position in file.
            f.seek(initial_offset)
            raise

        if sync_event:
            sync_event.completed = f.tell()

        return session_start.session_id

    @_retry_on_error(DataCorruptionError, MAX_TRANSFER_RETRIES)
    def _upload_session_append_helper(
        self,
        f: BinaryIO,
        session_id: str,
        dbx_path: str,
        sync_event: SyncEvent | None,
        old_stat: os.stat_result,
    ) -> None:
        initial_offset = f.tell()
        data = f.read(self.UPLOAD_REQUEST_CHUNK_SIZE)

        cursor = files.UploadSessionCursor(
            session_id=session_id,
            offset=initial_offset,
        )

        if file_was_modified(os.stat(f.fileno()), old_stat):
            # Close upload session and throw error.
            with convert_api_errors(dbx_path=dbx_path):
                self.dbx.files_upload_session_append_v2(b"", cursor, close=True)
            raise DataChangedError("File was modified during read")

        with convert_api_errors(dbx_path=dbx_path):
            try:
                self.dbx.files_upload_session_append_v2(
                    self._throttled_upload_iter(data),
                    cursor,
                    content_hash=get_hash(data),
                )
            except exceptions.ApiError as exc:
                # Return to position in file requested by Dropbox API if requested.
                # DataCorruptionError will then be handled by retry logic.
                correct_offset = get_correct_offset(exc, initial_offset)
                f.seek(correct_offset)
                raise
            except Exception:
                # Return to previous position in file.
                f.seek(initial_offset)
                raise

        if sync_event:
            sync_event.completed = f.tell()

    @_retry_on_error(DataCorruptionError, MAX_TRANSFER_RETRIES)
    def _upload_session_finish_helper(
        self,
        f: BinaryIO,
        session_id: str,
        dbx_path: str,
        mode: files.WriteMode,
        autorename: bool,
        sync_event: SyncEvent | None,
        old_stat: os.stat_result,
    ) -> files.FileMetadata:
        initial_offset = f.tell()
        data = f.read(self.UPLOAD_REQUEST_CHUNK_SIZE)
        stat = os.stat(f.fileno())

        cursor = files.UploadSessionCursor(
            session_id=session_id,
            offset=initial_offset,
        )

        if file_was_modified(os.stat(f.fileno()), old_stat):
            # Close upload session and throw error.
            with convert_api_errors(dbx_path=dbx_path):
                self.dbx.files_upload_session_append_v2(b"", cursor, close=True)
            raise DataChangedError("File was modified during read")

        # Finish upload session and return metadata.
        commit = files.CommitInfo(
            path=dbx_path,
            client_modified=datetime.utcfromtimestamp(stat.st_mtime),
            autorename=autorename,
            mode=mode,
        )

        with convert_api_errors(dbx_path=dbx_path):
            try:
                md = self.dbx.files_upload_session_finish(
                    self._throttled_upload_iter(data),
                    cursor,
                    commit,
                    content_hash=get_hash(data),
                )
            except exceptions.ApiError as exc:
                # Return to position in file requested by Dropbox API if requested.
                # DataCorruptionError will then be handled by retry logic.
                correct_offset = get_correct_offset(exc, initial_offset)
                f.seek(correct_offset)
                raise
            except Exception:
                # Return to previous position in file.
                f.seek(initial_offset)
                raise

        if sync_event:
            sync_event.completed = sync_event.size

        return md

    def remove(
        self, dbx_path: str, parent_rev: str | None = None
    ) -> FileMetadata | FolderMetadata:
        """
        Removes a file / folder from Dropbox.

        :param dbx_path: Path to file on Dropbox.
        :param parent_rev: Perform delete if given "rev" matches the existing file's
            latest "rev". This field does not support deleting a folder.
        :returns: Metadata of deleted item.
        """
        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_delete_v2(dbx_path, parent_rev=parent_rev)
        return convert_metadata(res.metadata)

    def remove_batch(
        self, entries: Sequence[tuple[str, str | None]], batch_size: int = 900
    ) -> list[FileMetadata | FolderMetadata | MaestralApiError]:
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
        result_list: list[FileMetadata | FolderMetadata | MaestralApiError] = []

        # Up two ~ 1,000 entries allowed per batch:
        # https://www.dropbox.com/developers/reference/data-ingress-guide
        for chunk in chunks(list(entries), n=batch_size):
            arg = [files.DeleteArg(e[0], e[1]) for e in chunk]

            with convert_api_errors():
                res = self.dbx.files_delete_batch(arg)

            if res.is_complete():
                batch_res = res.get_complete()
                res_entries.extend(batch_res.entries)

            elif res.is_async_job_id():
                async_job_id = res.get_async_job_id()

                time.sleep(0.5)

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
                result_list.append(convert_metadata(entry.get_success().metadata))
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

    def move(
        self, dbx_path: str, new_path: str, autorename: bool = False
    ) -> FileMetadata | FolderMetadata:
        """
        Moves / renames files or folders on Dropbox.

        :param dbx_path: Path to file/folder on Dropbox.
        :param new_path: New path on Dropbox to move to.
        :param autorename: Have the Dropbox server try to rename the item in case of a
            conflict.
        :returns: Metadata of moved item.
        """
        with convert_api_errors(dbx_path=new_path):
            res = self.dbx.files_move_v2(
                dbx_path,
                new_path,
                allow_shared_folder=True,
                allow_ownership_transfer=True,
                autorename=autorename,
            )
        return convert_metadata(res.metadata)

    def make_dir(self, dbx_path: str, autorename: bool = False) -> FolderMetadata:
        """
        Creates a folder on Dropbox.

        :param dbx_path: Path of Dropbox folder.
        :param autorename: Have the Dropbox server try to rename the item in case of a
            conflict.
        :returns: Metadata of created folder.
        """
        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.files_create_folder_v2(dbx_path, autorename)

        md = cast(files.FolderMetadata, res.metadata)
        return convert_metadata(md)

    def make_dir_batch(
        self,
        dbx_paths: list[str],
        batch_size: int = 900,
        autorename: bool = False,
        force_async: bool = False,
    ) -> list[FolderMetadata | MaestralApiError]:
        """
        Creates multiple folders on Dropbox in a batch job.

        :param dbx_paths: List of dropbox folder paths.
        :param batch_size: Number of folders to create in each batch. Dropbox allows
            batches of up to 1,000 folders. Larger values will be capped automatically.
        :param autorename: Have the Dropbox server try to rename the item in case of a
            conflict.
        :param force_async: Whether to force asynchronous creation on Dropbox servers.
        :returns: List of Metadata for created folders or SyncError for failures.
            Entries will be in the same order as given paths.
        """
        batch_size = clamp(batch_size, 1, 1000)

        entries = []
        result_list: list[FolderMetadata | MaestralApiError] = []

        with convert_api_errors():
            # Up two ~ 1,000 entries allowed per batch:
            # https://www.dropbox.com/developers/reference/data-ingress-guide
            for chunk in chunks(dbx_paths, n=batch_size):
                res = self.dbx.files_create_folder_batch(chunk, autorename, force_async)
                if res.is_complete():
                    batch_res = res.get_complete()
                    entries.extend(batch_res.entries)
                elif res.is_async_job_id():
                    async_job_id = res.get_async_job_id()

                    time.sleep(0.5)
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
                                chunk, round(batch_size / 2), autorename, force_async
                            )
                            result_list.extend(res_list)

        for i, entry in enumerate(entries):
            if entry.is_success():
                result_list.append(convert_metadata(entry.get_success().metadata))
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

    def share_dir(
        self, dbx_path: str, force_async: bool = False
    ) -> FolderMetadata | None:
        """
        Converts a Dropbox folder to a shared folder. Creates the folder if it does not
        exist. May return None if the folder is immediately deleted after creation.

        :param dbx_path: Path of Dropbox folder.
        :param force_async: Whether to force async creation of the Dropbox directory.
            This only changes implementation details and is currently used to reliably
            test the async route.
        :returns: Metadata of shared folder.
        """
        dbx_path = "" if dbx_path == "/" else dbx_path

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.sharing_share_folder(dbx_path, force_async=force_async)

        if res.is_complete():
            shared_folder_md = res.get_complete()

        elif res.is_async_job_id():
            async_job_id = res.get_async_job_id()

            time.sleep(0.2)

            with convert_api_errors(dbx_path=dbx_path):
                job_status = self.dbx.sharing_check_share_job_status(async_job_id)

            while job_status.is_in_progress():
                time.sleep(0.2)

                with convert_api_errors(dbx_path=dbx_path):
                    job_status = self.dbx.sharing_check_share_job_status(async_job_id)

            if job_status.is_complete():
                shared_folder_md = job_status.get_complete()

            elif job_status.is_failed():
                error = job_status.get_failed()
                exc = exceptions.ApiError(
                    error=error,
                    user_message_locale="",
                    user_message_text="",
                    request_id="",
                )
                raise dropbox_to_maestral_error(exc)
            else:
                raise MaestralApiError(
                    "Could not create shared folder",
                    "Unexpected response from sharing/check_share_job_status "
                    f"endpoint: {res}.",
                )
        else:
            raise MaestralApiError(
                "Could not create shared folder",
                f"Unexpected response from sharing/share_folder endpoint: {res}.",
            )

        md = self.get_metadata(f"ns:{shared_folder_md.shared_folder_id}")
        if isinstance(md, FolderMetadata):
            return md
        else:
            return None

    def get_latest_cursor(
        self, dbx_path: str, include_non_downloadable_files: bool = False
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
            )

        return res.cursor

    def list_folder(
        self,
        dbx_path: str,
        recursive: bool = False,
        include_deleted: bool = False,
        include_mounted_folders: bool = True,
        include_non_downloadable_files: bool = False,
    ) -> ListFolderResult:
        """
        Lists the contents of a folder on Dropbox. Similar to
        :meth:`list_folder_iterator` but returns all entries in a single
        :class:`core.ListFolderResult` instance.

        :param dbx_path: Path of folder on Dropbox.
        :param dbx_path: Path of folder on Dropbox.
        :param recursive: If true, the list folder operation will be applied recursively
            to all subfolders and the response will contain contents of all subfolders.
        :param include_deleted: If true, the results will include entries for files and
            folders that used to exist but were deleted.
        :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
        :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
        :returns: Content of given folder.
        """
        iterator = self.list_folder_iterator(
            dbx_path,
            recursive=recursive,
            include_deleted=include_deleted,
            include_mounted_folders=include_mounted_folders,
            include_non_downloadable_files=include_non_downloadable_files,
        )

        return self.flatten_results(list(iterator))

    def list_folder_iterator(
        self,
        dbx_path: str,
        recursive: bool = False,
        include_deleted: bool = False,
        include_mounted_folders: bool = True,
        limit: int | None = None,
        include_non_downloadable_files: bool = False,
    ) -> Iterator[ListFolderResult]:
        """
        Lists the contents of a folder on Dropbox. Returns an iterator yielding
        :class:`core.ListFolderResult` instances. The number of entries
        returned in each iteration corresponds to the number of entries returned by a
        single Dropbox API call and will be typically around 500.

        :param dbx_path: Path of folder on Dropbox.
        :param recursive: If true, the list folder operation will be applied recursively
            to all subfolders and the response will contain contents of all subfolders.
        :param include_deleted: If true, the results will include entries for files and
            folders that used to exist but were deleted.
        :param bool include_mounted_folders: If true, the results will include
            entries under mounted folders which includes app folder, shared
            folder and team folder.
        :param Nullable[int] limit: The maximum number of results to return per
            request. Note: This is an approximate number and there can be
            slightly more entries returned in some cases.
        :param bool include_non_downloadable_files: If true, include files that
            are not downloadable, i.e. Google Docs.
        :returns: Iterator over content of given folder.
        """
        with convert_api_errors(dbx_path):
            dbx_path = "" if dbx_path == "/" else dbx_path

            res = self.dbx.files_list_folder(
                dbx_path,
                recursive=recursive,
                include_deleted=include_deleted,
                include_mounted_folders=include_mounted_folders,
                limit=limit,
                include_non_downloadable_files=include_non_downloadable_files,
            )

            yield convert_list_folder_result(res)

            while res.has_more:
                res = self._list_folder_continue_helper(res.cursor)
                yield convert_list_folder_result(res)

    @_retry_on_error(
        requests.exceptions.ReadTimeout, MAX_LIST_FOLDER_RETRIES, backoff=3
    )
    def _list_folder_continue_helper(self, cursor: str) -> files.ListFolderResult:
        return self.dbx.files_list_folder_continue(cursor)

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

    def list_remote_changes(self, last_cursor: str) -> ListFolderResult:
        """
        Lists changes to remote Dropbox since ``last_cursor``. Same as
        :meth:`list_remote_changes_iterator` but fetches all changes first and returns
        a single :class:`core.ListFolderResult`. This may be useful if you want
        to fetch all changes in advance before starting to process them.

        :param last_cursor: Last to cursor to compare for changes.
        :returns: Remote changes since given cursor.
        """
        iterator = self.list_remote_changes_iterator(last_cursor)
        return self.flatten_results(list(iterator))

    def list_remote_changes_iterator(
        self, last_cursor: str
    ) -> Iterator[ListFolderResult]:
        """
        Lists changes to the remote Dropbox since ``last_cursor``. Returns an iterator
        yielding :class:`core.ListFolderResult` instances. The number of
        entries returned in each iteration corresponds to the number of entries returned
        by a single Dropbox API call and will be typically around 500.

        Call this after :meth:`wait_for_remote_changes` returns ``True``.

        :param last_cursor: Last to cursor to compare for changes.
        :returns: Iterator over remote changes since given cursor.
        """
        with convert_api_errors():
            res = self.dbx.files_list_folder_continue(last_cursor)

            yield convert_list_folder_result(res)

            while res.has_more:
                res = self.dbx.files_list_folder_continue(res.cursor)
                yield convert_list_folder_result(res)

    def create_shared_link(
        self,
        dbx_path: str,
        visibility: LinkAudience = LinkAudience.Public,
        access_level: LinkAccessLevel = LinkAccessLevel.Viewer,
        allow_download: bool | None = None,
        password: str | None = None,
        expires: datetime | None = None,
    ) -> SharedLinkMetadata:
        """
        Creates a shared link for the given path. Some options are only available for
        Professional and Business accounts. Note that the requested visibility and
        access level for the link may not be granted, depending on the Dropbox folder or
        team settings. Check the returned link metadata to verify the visibility and
        access level.

        :param dbx_path: Dropbox path to file or folder to share.
        :param visibility: The visibility of the shared link. Can be public, team-only,
            or no-one. In case of the latter, the link merely points the user to the
            content and does not grant additional rights to the user. Users of this link
            can only access the content with their pre-existing access rights.
        :param access_level: The level of access granted with the link. Can be viewer,
            editor, or max for maximum possible access level.
        :param allow_download: Whether to allow download capabilities for the link.
        :param password: If given, enables password protection for the link.
        :param expires: Expiry time for shared link. If no timezone is given, assume
            UTC. May not be supported for all account types.
        :returns: Metadata for shared link.
        """
        # Convert timestamp to utc time if not naive.
        if expires is not None:
            has_timezone = expires.tzinfo and expires.tzinfo.utcoffset(expires)
            if has_timezone:
                expires.astimezone(timezone.utc)

        settings = sharing.SharedLinkSettings(
            require_password=password is not None,
            link_password=password,
            expires=expires,
            audience=sharing.LinkAudience(visibility.value),
            access=sharing.RequestedLinkAccessLevel(access_level.value),
            allow_download=allow_download,
        )

        with convert_api_errors(dbx_path=dbx_path):
            res = self.dbx.sharing_create_shared_link_with_settings(dbx_path, settings)

        return convert_shared_link_metadata(res)

    def revoke_shared_link(self, url: str) -> None:
        """
        Revokes a shared link.

        :param url: URL to revoke.
        """
        with convert_api_errors():
            self.dbx.sharing_revoke_shared_link(url)

    def list_shared_links(
        self, dbx_path: str | None = None
    ) -> list[SharedLinkMetadata]:
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
            results.append(convert_list_shared_link_result(res))

            while results[-1].has_more:
                res = self.dbx.sharing_list_shared_links(dbx_path, results[-1].cursor)
                results.append(convert_list_shared_link_result(res))

        return self.flatten_results(results).entries

    @staticmethod
    def flatten_results(results: list[PRT]) -> PRT:
        """
        Flattens a sequence listing results from a pagination to a single result with
        the cursor of the last result in the list.

        :param results: List of results to flatten.
        :returns: Flattened result.
        """
        all_entries = [entry for res in results for entry in res.entries]
        result_cls = type(results[0])
        return result_cls(
            entries=all_entries, has_more=False, cursor=results[-1].cursor
        )


# ==== type conversions ================================================================


def convert_account(res: users.Account) -> Account:
    return Account(
        res.account_id,
        res.name.display_name,
        res.email,
        res.email_verified,
        res.profile_photo_url,
        res.disabled,
    )


def convert_full_account(res: users.FullAccount) -> FullAccount:
    if res.account_type.is_basic():
        account_type = AccountType.Basic
    elif res.account_type.is_pro():
        account_type = AccountType.Pro
    elif res.account_type.is_business():
        account_type = AccountType.Business
    else:
        account_type = AccountType.Other

    root_info: RootInfo

    if isinstance(res.root_info, common.TeamRootInfo):
        root_info = TeamRootInfo(
            res.root_info.root_namespace_id,
            res.root_info.home_namespace_id,
            res.root_info.home_path,
        )
    else:
        root_info = UserRootInfo(
            res.root_info.root_namespace_id, res.root_info.home_namespace_id
        )

    team = Team(res.team.id, res.team.name) if res.team else None

    return FullAccount(
        res.account_id,
        res.name.display_name,
        res.email,
        res.email_verified,
        res.profile_photo_url,
        res.disabled,
        res.country,
        res.locale,
        team,
        res.team_member_id,
        account_type,
        root_info,
    )


def convert_space_usage(res: users.SpaceUsage) -> SpaceUsage:
    if res.allocation.is_team():
        team_allocation = res.allocation.get_team()
        if team_allocation.user_within_team_space_allocated == 0:
            # Unlimited space within team allocation.
            allocated = team_allocation.allocated
        else:
            allocated = team_allocation.user_within_team_space_allocated
        return SpaceUsage(
            res.used,
            allocated,
            TeamSpaceUsage(team_allocation.used, team_allocation.allocated),
        )
    elif res.allocation.is_individual():
        individual_allocation = res.allocation.get_individual()
        return SpaceUsage(res.used, individual_allocation.allocated, None)
    else:
        return SpaceUsage(res.used, 0, None)


def convert_metadata(res):  # type:ignore[no-untyped-def]
    if isinstance(res, files.FileMetadata):
        symlink_target = res.symlink_info.target if res.symlink_info else None
        shared = res.sharing_info is not None or res.has_explicit_shared_members
        modified_by = res.sharing_info.modified_by if res.sharing_info else None
        return FileMetadata(
            res.name,
            res.path_lower,
            res.path_display,
            res.id,
            res.client_modified.replace(tzinfo=timezone.utc),
            res.server_modified.replace(tzinfo=timezone.utc),
            res.rev,
            res.size,
            symlink_target,
            shared,
            modified_by,
            res.is_downloadable,
            res.content_hash,
        )
    elif isinstance(res, files.FolderMetadata):
        shared = res.sharing_info is not None
        return FolderMetadata(
            res.name, res.path_lower, res.path_display, res.id, shared
        )
    elif isinstance(res, files.DeletedMetadata):
        return DeletedMetadata(res.name, res.path_lower, res.path_display)
    else:
        raise RuntimeError(f"Unsupported metadata {res}")


def convert_list_folder_result(res: files.ListFolderResult) -> ListFolderResult:
    entries = [convert_metadata(e) for e in res.entries]
    return ListFolderResult(entries, res.has_more, res.cursor)


def convert_shared_link_metadata(res: sharing.SharedLinkMetadata) -> SharedLinkMetadata:
    effective_audience = LinkAudience.Other
    require_password = res.link_permissions.require_password is True

    if res.link_permissions.effective_audience:
        if res.link_permissions.effective_audience.is_public():
            effective_audience = LinkAudience.Public
        elif res.link_permissions.effective_audience.is_team():
            effective_audience = LinkAudience.Team
        elif res.link_permissions.effective_audience.is_no_one():
            effective_audience = LinkAudience.NoOne

    elif res.link_permissions.resolved_visibility:
        if res.link_permissions.resolved_visibility.is_public():
            effective_audience = LinkAudience.Public
        elif res.link_permissions.resolved_visibility.is_team_only():
            effective_audience = LinkAudience.Team
        elif res.link_permissions.resolved_visibility.is_password():
            require_password = True
        elif res.link_permissions.resolved_visibility.is_team_and_password():
            effective_audience = LinkAudience.Team
            require_password = True
        elif res.link_permissions.resolved_visibility.is_no_one():
            effective_audience = LinkAudience.NoOne

    link_access_level = LinkAccessLevel.Other

    if res.link_permissions.link_access_level:
        if res.link_permissions.link_access_level.is_viewer():
            link_access_level = LinkAccessLevel.Viewer
        elif res.link_permissions.link_access_level.is_editor():
            link_access_level = LinkAccessLevel.Editor

    link_permissions = LinkPermissions(
        res.link_permissions.can_revoke,
        res.link_permissions.allow_download,
        effective_audience,
        link_access_level,
        require_password,
    )

    return SharedLinkMetadata(
        res.url,
        res.name,
        res.path_lower,
        res.expires.replace(tzinfo=timezone.utc) if res.expires else None,
        link_permissions,
    )


def convert_list_shared_link_result(
    res: sharing.ListSharedLinksResult,
) -> ListSharedLinkResult:
    entries = [convert_shared_link_metadata(e) for e in res.links]
    return ListSharedLinkResult(entries, res.has_more, res.cursor)


# ==== helper methods ==================================================================


def get_correct_offset(exc: exceptions.ApiError, initial_offset: int) -> int:
    if (
        isinstance(exc.error, files.UploadSessionFinishError)
        and exc.error.is_lookup_failed()
        and exc.error.get_lookup_failed().is_incorrect_offset()
    ):
        return exc.error.get_lookup_failed().get_incorrect_offset().correct_offset
    if (
        isinstance(exc.error, files.UploadSessionAppendError)
        and exc.error.is_incorrect_offset()
    ):
        return exc.error.get_incorrect_offset().correct_offset
    return initial_offset


def file_was_modified(news_stat: os.stat_result, old_stat: os.stat_result) -> bool:
    """Checks for changes to a file by comparing stat results.

    :raises DataChangedError: if there were changes to the file.
    """
    return news_stat.st_ctime_ns != old_stat.st_ctime_ns
