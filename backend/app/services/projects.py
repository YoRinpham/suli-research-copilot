from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.persistence.models import Project, now_utc
from app.services.errors import ServiceError


class ProjectService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, title: str, research_question: str) -> Project:
        project = Project(title=title.strip(), research_question=research_question.strip())
        self.session.add(project)
        self.session.commit()
        self.session.refresh(project)
        return project

    def list(self) -> list[Project]:
        return list(self.session.scalars(select(Project).order_by(Project.created_at, Project.id)))

    def get(self, project_id: str) -> Project:
        project = self.session.get(Project, project_id)
        if project is None:
            raise ServiceError(404, "not_found", "Project not found", "project")
        return project

    def update(
        self,
        project_id: str,
        expected_question_revision: int,
        title: str | None,
        research_question: str | None,
    ) -> Project:
        current = self.get(project_id)
        values: dict = {"updated_at": now_utc()}
        if title is not None:
            values["title"] = title.strip()
        if research_question is not None:
            question = research_question.strip()
            values["research_question"] = question
            if question != current.research_question:
                values["question_revision"] = Project.question_revision + 1
        result = self.session.execute(
            update(Project)
            .where(Project.id == project_id, Project.question_revision == expected_question_revision)
            .values(**values)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ServiceError(409, "stale_revision", "Research question revision is stale", "project")
        self.session.commit()
        self.session.refresh(current)
        return current
