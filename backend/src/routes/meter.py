from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.database import db_session
from src.controllers.auth import current_user, writer
from src.models import MeterObservationSet
from src.schemas.meter import MeterBatchInput
from src.services import meter as service
from src.utils.serialization import rows

router = APIRouter(prefix="/meter")


@router.get("/batches")
def batches(db=Depends(db_session), user=Depends(current_user)):
    return service.list_batches(db)


@router.post("/batches")
def create_batch(payload: MeterBatchInput, db=Depends(db_session), user=Depends(writer)):
    summary, reused = service.create(db, payload)
    return {"reused": reused, "observation_set": summary}


@router.get("/sets")
def sets(db=Depends(db_session), user=Depends(current_user)):
    return service.list_sets(db)


@router.get("/sets/{set_id}")
def set_detail(set_id: str, db=Depends(db_session), user=Depends(current_user)):
    return service.detail(db, set_id)
