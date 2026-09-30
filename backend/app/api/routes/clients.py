import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_admin
from app.models.client import Client
from app.models.google_connection import GoogleConnection
from app.models.user import User
from app.schemas.client import ClientCreate, ClientOut
from app.services.activity_service import log_activity

router = APIRouter(prefix="/clients", tags=["clients"])


def _to_out(client: Client, db: Session) -> ClientOut:
    connected = db.query(GoogleConnection).filter(GoogleConnection.client_id == client.id).first() is not None
    out = ClientOut.model_validate(client)
    out.google_connected = connected
    return out


@router.post("", response_model=ClientOut, status_code=201)
def create_client(payload: ClientCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    client = Client(owner_user_id=current_user.id, name=payload.name, website_url=payload.website_url)
    db.add(client)
    db.commit()
    db.refresh(client)
    log_activity(current_user, "client_created", client)
    return _to_out(client, db)


@router.get("", response_model=list[ClientOut])
def list_clients(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    clients = db.query(Client).order_by(Client.created_at.desc()).all()
    return [_to_out(c, db) for c in clients]


@router.get("/{client_id}", response_model=ClientOut)
def get_client(client_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return _to_out(client, db)
