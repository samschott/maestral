# type: ignore
# flake8: noqa

import errno

import pytest
from dropbox import exceptions
from dropbox.files import *
from dropbox.async_ import *
from dropbox.users import *
from dropbox.sharing import *
from dropbox.auth import *
from dropbox.common import *

from maestral.exceptions import (
    MaestralApiError,
    InvalidDbidError,
    DropboxAuthError,
    TokenExpiredError,
    TokenRevokedError,
    CursorResetError,
    BadInputError,
    OutOfMemoryError,
    SharedLinkError,
    SyncError,
    InsufficientPermissionsError,
    InsufficientSpaceError,
    PathError,
    NotFoundError,
    ConflictError,
    IsAFolderError,
    NotAFolderError,
    DropboxServerError,
    RestrictedContentError,
    UnsupportedFileError,
    FileSizeError,
    FileReadError,
    FileConflictError,
    FolderConflictError,
    DataCorruptionError,
)
from maestral.exceptions import PathRootError as MPRE
from maestral.errorhandling import (
    os_to_maestral_error,
    dropbox_to_maestral_error,
    get_lookup_error_msg,
    get_write_error_msg,
    get_session_lookup_error_msg,
)


@pytest.mark.parametrize(
    "number,maestral_exc",
    [
        (errno.EPERM, InsufficientPermissionsError),
        (errno.ENOENT, NotFoundError),
        (errno.EEXIST, ConflictError),
        (errno.EISDIR, IsAFolderError),
        (errno.ENOTDIR, NotAFolderError),
        (errno.ENAMETOOLONG, PathError),
        (errno.EINVAL, PathError),
        (errno.EFBIG, FileSizeError),
        (errno.ENOSPC, InsufficientSpaceError),
        (errno.EFAULT, FileReadError),
        (errno.ENOMEM, OutOfMemoryError),
    ],
)
def test_os_to_maestral_error(number: int, maestral_exc):
    os_error = OSError(number, "error")
    converted = os_to_maestral_error(os_error)
    assert isinstance(converted, maestral_exc)


@pytest.mark.parametrize(
    "error,maestral_exc",
    [
        (LookupError.malformed_path(None), PathError),
        (LookupError.not_file, IsAFolderError),
        (LookupError.not_folder, NotAFolderError),
        (LookupError.not_found, NotFoundError),
        (LookupError.restricted_content, RestrictedContentError),
        (LookupError.unsupported_content_type, UnsupportedFileError),
        (LookupError.locked, InsufficientPermissionsError),
    ],
)
def test_get_lookup_error_msg(error, maestral_exc):
    text, err_cls = get_lookup_error_msg(error)
    assert err_cls is maestral_exc


@pytest.mark.parametrize(
    "error,maestral_exc",
    [
        (WriteError.conflict(WriteConflictError.file), FileConflictError),
        (WriteError.conflict(WriteConflictError.folder), FolderConflictError),
        (WriteError.conflict(WriteConflictError.file_ancestor), FileConflictError),
        (WriteError.disallowed_name, PathError),
        (WriteError.insufficient_space, InsufficientSpaceError),
        (WriteError.malformed_path(None), PathError),
        (WriteError.no_write_permission, InsufficientPermissionsError),
        (WriteError.team_folder, SyncError),
        (WriteError.too_many_write_operations, SyncError),
    ],
)
def test_get_write_error_msg(error, maestral_exc):
    text, err_cls = get_write_error_msg(error)
    assert err_cls is maestral_exc


@pytest.mark.parametrize(
    "error,maestral_exc",
    [
        (UploadSessionLookupError.closed, SyncError),
        (
            UploadSessionLookupError.incorrect_offset(UploadSessionOffsetError(20)),
            DataCorruptionError,
        ),
        (UploadSessionLookupError.not_closed, SyncError),
        (UploadSessionLookupError.not_found, SyncError),
        (UploadSessionLookupError.too_large, FileSizeError),
        (UploadSessionAppendError.content_hash_mismatch, DataCorruptionError),
    ],
)
def test_get_session_lookup_error_msg(error, maestral_exc):
    text, err_cls = get_session_lookup_error_msg(error)
    assert err_cls is maestral_exc


@pytest.mark.parametrize(
    "error,maestral_exc",
    [
        (RelocationError.cant_copy_shared_folder, SyncError),
        (RelocationError.cant_move_folder_into_itself, ConflictError),
        (RelocationError.cant_move_shared_folder, SyncError),
        (RelocationError.cant_nest_shared_folder, SyncError),
        (RelocationError.cant_transfer_ownership, SyncError),
        (RelocationError.duplicated_or_nested_paths, SyncError),
        (RelocationError.from_lookup(LookupError.not_found), NotFoundError),
        (RelocationError.from_write(WriteError.team_folder), SyncError),
        (RelocationError.to(WriteError.team_folder), SyncError),
        (RelocationError.insufficient_quota, InsufficientSpaceError),
        (RelocationError.internal_error, DropboxServerError),
        (RelocationError.too_many_files, SyncError),
        (RelocationError.other, MaestralApiError),
        (CreateFolderError.path(WriteError.team_folder), SyncError),
        (DeleteError.path_lookup(LookupError.not_found), NotFoundError),
        (DeleteError.path_write(WriteError.team_folder), SyncError),
        (DeleteError.too_many_files, SyncError),
        (DeleteError.too_many_write_operations, SyncError),
        (DeleteError.other, MaestralApiError),
        (
            UploadError.path(
                UploadWriteFailed(reason=WriteError.team_folder, upload_session_id="")
            ),
            SyncError,
        ),
        (UploadError.properties_error, MaestralApiError),
        (UploadError.content_hash_mismatch, DataCorruptionError),
        (UploadError.other, MaestralApiError),
        (
            UploadSessionStartError.concurrent_session_close_not_allowed,
            MaestralApiError,
        ),
        (UploadSessionStartError.concurrent_session_data_not_allowed, MaestralApiError),
        (UploadSessionStartError.other, MaestralApiError),
        (
            UploadSessionFinishError.lookup_failed(UploadSessionLookupError.not_found),
            MaestralApiError,
        ),
        (UploadSessionFinishError.path(WriteError.team_folder), SyncError),
        (UploadSessionFinishError.properties_error, MaestralApiError),
        (UploadSessionFinishError.too_many_write_operations, SyncError),
        (UploadSessionFinishError.content_hash_mismatch, DataCorruptionError),
        (UploadSessionFinishError.other, MaestralApiError),
        (UploadSessionLookupError.too_large, FileSizeError),
        (UploadSessionLookupError.other, MaestralApiError),
        (DownloadError.path(LookupError.not_found), NotFoundError),
        (DownloadError.unsupported_file, UnsupportedFileError),
        (DownloadError.other, MaestralApiError),
        (ListFolderError.path(LookupError.not_found), NotFoundError),
        (ListFolderError.other, MaestralApiError),
        (ListFolderContinueError.path(LookupError.not_found), NotFoundError),
        (ListFolderContinueError.reset, CursorResetError),
        (ListFolderContinueError.other, MaestralApiError),
        (ListFolderLongpollError.reset, CursorResetError),
        (ListFolderLongpollError.other, MaestralApiError),
        (PollError.internal_error, DropboxServerError),
        (PollError.other, MaestralApiError),
        (ListRevisionsError.path(LookupError.not_found), NotFoundError),
        (ListRevisionsError.other, MaestralApiError),
        (RestoreError.invalid_revision, NotFoundError),
        (RestoreError.path_lookup(LookupError.not_found), NotFoundError),
        (RestoreError.path_write(WriteError.team_folder), SyncError),
        (RestoreError.in_progress, SyncError),
        (RestoreError.other, MaestralApiError),
        (GetMetadataError.path(LookupError.not_found), NotFoundError),
        (GetAccountError.no_account, InvalidDbidError),
        (GetAccountError.other, MaestralApiError),
        (CreateSharedLinkWithSettingsError.access_denied, InsufficientPermissionsError),
        (CreateSharedLinkWithSettingsError.email_not_verified, SharedLinkError),
        (CreateSharedLinkWithSettingsError.path(LookupError.not_found), NotFoundError),
        (
            CreateSharedLinkWithSettingsError.settings_error(
                SharedLinkSettingsError.invalid_settings
            ),
            SharedLinkError,
        ),
        (
            CreateSharedLinkWithSettingsError.shared_link_already_exists(
                SharedLinkAlreadyExistsMetadata.other
            ),
            SharedLinkError,
        ),
        (RevokeSharedLinkError("shared_link_not_found"), NotFoundError),
        (
            RevokeSharedLinkError("shared_link_access_denied"),
            InsufficientPermissionsError,
        ),
        (RevokeSharedLinkError("shared_link_malformed"), SharedLinkError),
        (RevokeSharedLinkError("other"), MaestralApiError),
        (ListSharedLinksError.path(LookupError.not_found), NotFoundError),
        (ListSharedLinksError.reset, SharedLinkError),
        (ListSharedLinksError.other, MaestralApiError),
        (ShareFolderError.no_permission, InsufficientPermissionsError),
        (ShareFolderError.disallowed_shared_link_policy, InsufficientPermissionsError),
        (
            ShareFolderError.team_policy_disallows_member_policy,
            InsufficientPermissionsError,
        ),
        (ShareFolderError.email_unverified, MaestralApiError),
        (ShareFolderError.bad_path(SharePathError.is_file), FileConflictError),
        (ShareFolderError.bad_path(SharePathError.inside_shared_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.contains_shared_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.contains_app_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.contains_team_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_app_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.inside_app_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_public_folder), SyncError),
        (ShareFolderError.bad_path(SharePathError.inside_public_folder), SyncError),
        (
            ShareFolderError.bad_path(
                SharePathError.already_shared(SharedFolderMetadata())
            ),
            FolderConflictError,
        ),
        (ShareFolderError.bad_path(SharePathError.invalid_path), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_osx_package), SyncError),
        (ShareFolderError.bad_path(SharePathError.inside_osx_package), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_vault), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_vault_locked), SyncError),
        (ShareFolderError.bad_path(SharePathError.is_family), SyncError),
        (ShareFolderError.other, MaestralApiError),
    ],
)
def test_dropbox_api_to_maestral_error(error, maestral_exc):
    converted = dropbox_to_maestral_error(exceptions.ApiError("", error, "", ""))
    assert isinstance(converted, maestral_exc), f"{error} was incorrectly converted"


@pytest.mark.parametrize(
    "exception,maestral_exc",
    [
        (exceptions.AuthError("", AuthError.expired_access_token), TokenExpiredError),
        (exceptions.AuthError("", AuthError.invalid_access_token), TokenRevokedError),
        (exceptions.AuthError("", AuthError.user_suspended), DropboxAuthError),
        (
            exceptions.AuthError(
                "", AuthError.missing_scope(TokenScopeError(required_scope="test"))
            ),
            InsufficientPermissionsError,
        ),
        (exceptions.AuthError("", AuthError.other), MaestralApiError),
        (exceptions.BadInputError("", ""), BadInputError),
        (exceptions.InternalServerError("", "", ""), DropboxServerError),
        (exceptions.PathRootError("", PathRootError.no_permission), MPRE),
        (exceptions.PathRootError("", PathRootError.invalid_root(RootInfo())), MPRE),
        (exceptions.PathRootError("", PathRootError.other), MPRE),
    ],
)
def test_dropbox_to_maestral_error(exception, maestral_exc):
    converted = dropbox_to_maestral_error(exception)
    assert isinstance(converted, maestral_exc)
