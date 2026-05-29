"""Entity read endpoints for Footprinter HTTP API."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from footprinter.api import MAX_LIMIT
from footprinter.api.db import get_conn
from footprinter.services import (
    chat_service,
    client_service,
    email_service,
    file_service,
    folder_service,
    project_service,
    visit_service,
)
from footprinter.services.roles import Role

router = APIRouter(tags=["entities"])


def _or_404(result, entity_type: str, entity_id: int):
    """Return result or raise 404."""
    if result is None:
        raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")
    return result


# --- Files ---


@router.get("/files")
def list_files(
    conn=Depends(get_conn),
    project_id: Optional[int] = None,
    source: Optional[str] = Query(None, description="Comma-separated source filter"),
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    content_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    source_list = [s.strip() for s in source.split(",")] if source else None
    status_list = [s.strip() for s in status.split(",")] if status else None
    return file_service.list_(
        conn,
        role=Role.ADMIN,
        project_id=project_id,
        source=source_list,
        status=status_list,
        content_type=content_type,
        limit=limit,
        page=page,
    )


@router.get("/files/{file_id}")
def get_file(file_id: int, conn=Depends(get_conn)):
    return _or_404(file_service.get(conn, file_id, role=Role.ADMIN), "file", file_id)


# --- Emails ---


@router.get("/emails")
def list_emails(
    conn=Depends(get_conn),
    account: Optional[str] = None,
    client_id: Optional[int] = None,
    project_id: Optional[int] = None,
    query: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    sort_by: str = "received_at",
    order: str = "desc",
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    return email_service.list_(
        conn,
        role=Role.ADMIN,
        account=account,
        client_id=client_id,
        project_id=project_id,
        query=query,
        has_attachments=has_attachments,
        sort_by=sort_by,
        order=order,
        limit=limit,
        page=page,
    )


@router.get("/emails/{email_id}")
def get_email(email_id: int, conn=Depends(get_conn)):
    return _or_404(email_service.get(conn, email_id, role=Role.ADMIN), "email", email_id)


# --- Chats ---


@router.get("/chats")
def list_chats(
    conn=Depends(get_conn),
    account: Optional[str] = None,
    query: Optional[str] = None,
    sort_by: str = "modified_at",
    order: str = "desc",
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    status_list = [s.strip() for s in status.split(",")] if status else None
    return chat_service.list_(
        conn,
        role=Role.ADMIN,
        account=account,
        query=query,
        sort_by=sort_by,
        order=order,
        status=status_list,
        limit=limit,
        page=page,
    )


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, conn=Depends(get_conn)):
    return _or_404(chat_service.get(conn, chat_id, role=Role.ADMIN), "chat", chat_id)


# --- Projects ---


@router.get("/projects")
def list_projects(
    conn=Depends(get_conn),
    include: Optional[str] = Query(None, description="Comma-separated includes"),
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    client: Optional[str] = None,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    include_list = [s.strip() for s in include.split(",")] if include else None
    status_list = [s.strip() for s in status.split(",")] if status else None
    return project_service.list_(
        conn,
        role=Role.ADMIN,
        include=include_list,
        status=status_list,
        client=client,
        limit=limit,
        page=page,
    )


@router.get("/projects/{project_id}")
def get_project(project_id: int, conn=Depends(get_conn), include: Optional[str] = None):
    include_list = [s.strip() for s in include.split(",")] if include else None
    return _or_404(
        project_service.get(conn, project_id, role=Role.ADMIN, include=include_list),
        "project",
        project_id,
    )


# --- Clients ---


@router.get("/clients")
def list_clients(
    conn=Depends(get_conn),
    include: Optional[str] = Query(None, description="Comma-separated includes"),
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    include_list = [s.strip() for s in include.split(",")] if include else None
    status_list = [s.strip() for s in status.split(",")] if status else None
    return client_service.list_(
        conn,
        role=Role.ADMIN,
        include=include_list,
        status=status_list,
        limit=limit,
        page=page,
    )


@router.get("/clients/{client_id}")
def get_client(client_id: int, conn=Depends(get_conn), include: Optional[str] = None):
    include_list = [s.strip() for s in include.split(",")] if include else None
    return _or_404(
        client_service.get(conn, client_id, role=Role.ADMIN, include=include_list),
        "client",
        client_id,
    )


# --- Folders ---
# NOTE: /folders/by-path MUST be defined before /folders/{folder_id}
# to avoid FastAPI treating "by-path" as an int parameter.


@router.get("/folders/by-path")
def get_folder_by_path(path: str, conn=Depends(get_conn)):
    result = folder_service.get_by_path(conn, path, role=Role.ADMIN)
    if result is None:
        raise HTTPException(status_code=404, detail=f"folder at path '{path}' not found")
    return result


@router.get("/folders")
def list_folders(
    conn=Depends(get_conn),
    project_id: Optional[int] = None,
    depth: Optional[int] = 1,
    include_hidden: bool = False,
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    sort_by: str = "size",
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    status_list = [s.strip() for s in status.split(",")] if status else None
    return folder_service.list_(
        conn,
        role=Role.ADMIN,
        project_id=project_id,
        depth=depth,
        include_hidden=include_hidden,
        status=status_list,
        sort_by=sort_by,
        limit=limit,
        page=page,
    )


@router.get("/folders/{folder_id}")
def get_folder(folder_id: int, conn=Depends(get_conn)):
    return _or_404(folder_service.get(conn, folder_id, role=Role.ADMIN), "folder", folder_id)


# --- Visits ---


@router.get("/visits")
def list_visits(
    conn=Depends(get_conn),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    page: int = 1,
):
    return visit_service.list_(conn, role=Role.ADMIN, limit=limit, page=page)


@router.get("/visits/{entry_id}")
def get_visit(entry_id: int, conn=Depends(get_conn)):
    return _or_404(visit_service.get(conn, entry_id, role=Role.ADMIN), "visit", entry_id)
