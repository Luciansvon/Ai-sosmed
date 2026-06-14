# core/permission_gate.py
import asyncio
import logging
import uuid
import contextvars

logger = logging.getLogger('bima_core')

class PermissionTimeoutError(asyncio.TimeoutError):
    """Exception raised when a permission request times out."""
    pass

# ContextVar to store the current discord user ID across threads/tasks
current_user_id = contextvars.ContextVar("current_user_id", default="anon")

_pending_approvals = {}
_pending_users = {}   # user_id -> req_id
_req_to_user = {}     # req_id -> user_id
_revised_texts = {}   # user_id -> revised_text_str
_send_approval_request_cb = None
_main_loop = None
_user_locks = {}

def _get_user_lock(user_id: str) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

def set_main_loop(loop):
    global _main_loop
    _main_loop = loop
    logger.info("[PERMISSION_GATE] Main event loop registered.")

def get_main_loop():
    return _main_loop

def register_send_handler(cb):
    global _send_approval_request_cb
    _send_approval_request_cb = cb
    logger.info("[PERMISSION_GATE] Discord send handler registered.")

def get_pending_req_id_by_user(user_id: str) -> str | None:
    """Ambil req_id yang sedang menggantung untuk user tertentu."""
    return _pending_users.get(user_id)

def get_revised_text(user_id: str) -> str | None:
    """Ambil dan hapus (consume) teks revisi jika ada untuk user tersebut."""
    return _revised_texts.pop(user_id, None)

def resolve_approval_with_revision(req_id: str, revised_text: str):
    """Selesaikan request approval dengan status Setuju dan simpan teks revisinya."""
    user_id = _req_to_user.get(req_id)
    if user_id:
        _revised_texts[user_id] = revised_text
    resolve_approval(req_id, True)

async def request_permission(discord_user_id: str, action_type: str, details: str, attachment_paths: list[str] | None = None, raise_on_timeout: bool = False) -> bool:
    """Minta izin Bima sebelum mengeksekusi tindakan sensitif di sistem (async)."""
    if not discord_user_id or discord_user_id == "anon":
        logger.warning("[PERMISSION_GATE] Anonymous user or missing user ID. Deny by default.")
        return False

    if not _send_approval_request_cb:
        logger.warning("[PERMISSION_GATE] No Discord send handler registered. Deny by default.")
        return False

    lock = _get_user_lock(discord_user_id)
    async with lock:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        
        req_id = str(uuid.uuid4())
        _pending_approvals[req_id] = future
        _pending_users[discord_user_id] = req_id
        _req_to_user[req_id] = discord_user_id
        
        try:
            logger.info(f"[PERMISSION_GATE] Sending request {req_id} for user {discord_user_id} ({action_type})")
            success = await _send_approval_request_cb(req_id, discord_user_id, action_type, details, attachment_paths=attachment_paths)
            if not success:
                logger.warning(f"[PERMISSION_GATE] Failed to send approval request message for {req_id}")
                return False
                
            # Timeout 300 detik (5 menit), jika tidak direspon otomatis Tolak (False)
            approved = await asyncio.wait_for(future, timeout=300.0)
            logger.info(f"[PERMISSION_GATE] Request {req_id} resolved: {approved}")
            return approved
        except asyncio.TimeoutError:
            logger.warning(f"[PERMISSION_GATE] Request {req_id} timed out after 300s. Aborting.")
            if raise_on_timeout:
                raise PermissionTimeoutError("Request permission timed out after 300 seconds.")
            return False
        except Exception as e:
            logger.error(f"[PERMISSION_GATE] Error in request_permission for {req_id}: {e}", exc_info=True)
            return False
        finally:
            _pending_approvals.pop(req_id, None)
            _pending_users.pop(discord_user_id, None)
            _req_to_user.pop(req_id, None)

def resolve_approval(req_id: str, approved: bool):
    """Selesaikan request approval yang sedang menggantung."""
    future = _pending_approvals.get(req_id)
    if future and not future.done():
        future.set_result(approved)
        logger.info(f"[PERMISSION_GATE] Request {req_id} resolved via callback with: {approved}")

def check_permission_sync(action_type: str, details: str, attachment_paths: list[str] | None = None) -> bool:
    """Helper synchronous untuk dipanggil dari dalam sync CrewAI tools."""
    user_id = current_user_id.get("anon")
    logger.debug(f"[PERMISSION_GATE] Sync check: user={user_id}, action={action_type}")
    
    if user_id == "anon":
        logger.warning(f"[PERMISSION_GATE] Denied '{action_type}' because user is anonymous.")
        return False
        
    loop = get_main_loop()
    if not loop:
        logger.warning("[PERMISSION_GATE] No main event loop registered. Deny by default.")
        return False
        
    try:
        # Kirim coroutine ke main event loop dan tunggu hasilnya (blocking thread ini)
        future = asyncio.run_coroutine_threadsafe(
            request_permission(user_id, action_type, details, attachment_paths),
            loop
        )
        return future.result()
    except Exception as e:
        logger.error(f"[PERMISSION_GATE] Failed to execute sync check: {e}", exc_info=True)
        return False
