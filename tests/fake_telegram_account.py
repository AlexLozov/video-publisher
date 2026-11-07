"""A scriptable fake of the Telethon client (the app's ``AccountClient`` protocol).

Never talks to the network. One ``FakeTelegramAccount`` holds the "server" state; its
``factory`` is passed wherever the app expects a ``ClientFactory``.

* Login: the right code is ``world.code``; if ``world.password`` is set, 2FA is on.
  A successful login turns the session string into ``"session-ok"``.
* ``world.fail("method", exc, ...)`` makes the next calls of that method raise.
* ``@usernames`` in ``world.entities`` resolve directly; ids in ``world.dialog_entities``
  only after ``get_dialogs()`` (like a fresh Telethon session).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from telethon import errors

SESSION_OK = "session-ok"


class FakeSession:
    def __init__(self, value: str) -> None:
        self.value = value

    def save(self) -> str:
        return self.value


class FakeTelegramAccount:
    def __init__(self) -> None:
        self.code = "12345"
        self.password: str | None = None
        self.authorized_sessions = {SESSION_OK}
        self.failures: dict[str, list[BaseException]] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.entities: dict[Any, str] = {"@my_channel": "peer:@my_channel"}
        self.dialog_entities: dict[Any, str] = {-1001234567890: "peer:-1001234567890"}
        self.dialogs_loaded = False
        self.sent: list[dict[str, Any]] = []
        self.deleted_scheduled: list[tuple[Any, list[int]]] = []
        self.next_id = 100
        self.clients: list[FakeClient] = []

    def fail(self, method: str, *excs: BaseException) -> None:
        self.failures.setdefault(method, []).extend(excs)

    def factory(self, session: str, api_id: int, api_hash: str) -> FakeClient:
        client = FakeClient(self, session, api_id, api_hash)
        self.clients.append(client)
        return client

    def called(self, name: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(a, k) for n, a, k in self.calls if n == name]


class FakeClient:
    def __init__(self, world: FakeTelegramAccount, session: str, api_id: int, api_hash: str):
        self.world = world
        self.session = FakeSession(session)
        self.api_id = api_id
        self.api_hash = api_hash
        self.connected = False
        self.disconnected = False

    def _step(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.world.calls.append((name, args, kwargs))
        queue = self.world.failures.get(name)
        if queue:
            raise queue.pop(0)

    async def connect(self) -> None:
        self._step("connect")
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    async def is_user_authorized(self) -> bool:
        self._step("is_user_authorized")
        return self.session.value in self.world.authorized_sessions

    async def send_code_request(self, phone: str) -> Any:
        self._step("send_code_request", phone)
        # Telethon may switch data centre here, which changes the session.
        self.session.value = f"half-{phone}"
        return SimpleNamespace(phone_code_hash="hash-1")

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        *,
        password: str | None = None,
        phone_code_hash: str | None = None,
    ) -> Any:
        self._step("sign_in", phone, code, password=password, phone_code_hash=phone_code_hash)
        if password is None:
            if code != self.world.code:
                raise errors.PhoneCodeInvalidError(request=None)
            if self.world.password is not None:
                raise errors.SessionPasswordNeededError(request=None)
        elif password != self.world.password:
            raise errors.PasswordHashInvalidError(request=None)
        self.session.value = SESSION_OK
        return SimpleNamespace(id=1, first_name="Alex")

    async def log_out(self) -> bool:
        self._step("log_out")
        self.world.authorized_sessions.discard(self.session.value)
        return True

    async def get_input_entity(self, peer: Any) -> Any:
        self._step("get_input_entity", peer)
        if peer in self.world.entities:
            return self.world.entities[peer]
        if self.world.dialogs_loaded and peer in self.world.dialog_entities:
            return self.world.dialog_entities[peer]
        raise ValueError(f"Could not find the input entity for {peer!r}")

    async def get_dialogs(self, limit: int | None = None) -> Any:
        self._step("get_dialogs")
        self.world.dialogs_loaded = True
        return []

    async def upload_file(
        self, file: Any, *, file_size: int | None = None, file_name: str | None = None
    ) -> Any:
        self._step("upload_file", file, file_size=file_size, file_name=file_name)
        return SimpleNamespace(name=file_name, size=file_size)

    async def __call__(self, request: Any) -> Any:
        """Raw requests: only deleting scheduled messages is used by the app."""
        self._step("invoke", request)
        self.world.deleted_scheduled.append((request.peer, list(request.id)))
        return SimpleNamespace(updates=[])

    async def send_file(self, entity: Any, file: Any, **kwargs: Any) -> Any:
        self._step("send_file", entity, file, **kwargs)
        self.world.sent.append({"entity": entity, "file": file, **kwargs})
        message_id = self.world.next_id
        self.world.next_id += 1
        return SimpleNamespace(id=message_id)
