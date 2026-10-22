from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from app.persistence.database import get_session
from app.persistence.models import Project
from app.services.projects import ProjectService


router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session)) -> Project:
    return ProjectService(session).create(body.title, body.research_question)


@router.get("", response_model=list[ProjectRead])
def list_projects(session: Session = Depends(get_session)) -> list[Project]:
    return ProjectService(session).list()


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, session: Session = Depends(get_session)) -> Project:
    return ProjectService(session).get(project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: str, body: ProjectUpdate, session: Session = Depends(get_session)) -> Project:
    return ProjectService(session).update(
        project_id,
        body.expected_question_revision,
        body.title,
        body.research_question,
    )
