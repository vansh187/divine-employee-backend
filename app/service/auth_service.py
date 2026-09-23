"""Business logic for login, token refresh/rotation, logout and profile lookup.

No SQL and no asyncpg import here — all persistence goes through EmployeePersistence.
"""

from app.core.exceptions import NotFoundError, UnauthorizedError
from app.core.security import PasswordHasher, TokenService
from app.persistence.employee_persistence import EmployeePersistence
from app.schemas.auth_schema import EmployeeProfileResponse, TokenPairResponse


class AuthService:
    def __init__(
        self,
        employee_persistence: EmployeePersistence,
        token_service: TokenService,
        password_hasher: PasswordHasher,
    ) -> None:
        self._employee_persistence = employee_persistence
        self._token_service = token_service
        self._password_hasher = password_hasher

    async def login(self, email: str, password: str) -> TokenPairResponse:
        employee = await self._employee_persistence.get_by_email(email)
        if employee is None:
            raise UnauthorizedError("Invalid email or password")

        if not self._password_hasher.verify(password, employee["password_hash"]):
            raise UnauthorizedError("Invalid email or password")

        if employee["status"] != "ACTIVE":
            raise UnauthorizedError("This employee account is not active")

        return await self._issue_token_pair(str(employee["id"]), employee["employee_code"], employee["email"])

    async def refresh(self, refresh_token: str) -> TokenPairResponse:
        payload = self._token_service.decode_token(refresh_token, expected_type="refresh")
        employee_id = payload["sub"]

        token_hash = self._token_service.hash_token(refresh_token)
        # Revoke-and-check in one statement: a concurrent refresh with the same
        # token finds it already revoked instead of also minting a new pair.
        stored_token = await self._employee_persistence.consume_refresh_token(token_hash)
        if stored_token is None or str(stored_token["employee_id"]) != employee_id:
            raise UnauthorizedError("Refresh token has been revoked or expired")

        employee = await self._employee_persistence.get_by_id(employee_id)
        if employee is None or employee["status"] != "ACTIVE":
            raise UnauthorizedError("Employee account is not active")

        new_pair = await self._issue_token_pair(str(employee["id"]), employee["employee_code"], employee["email"])
        new_token_hash = self._token_service.hash_token(new_pair.refresh_token)
        await self._employee_persistence.set_replaced_by(token_hash, new_token_hash)
        return new_pair

    async def logout(self, refresh_token: str) -> None:
        token_hash = self._token_service.hash_token(refresh_token)
        await self._employee_persistence.revoke_refresh_token(token_hash)

    async def get_profile(self, employee_id: str) -> EmployeeProfileResponse:
        employee = await self._employee_persistence.get_by_id(employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")
        return EmployeeProfileResponse(**employee)

    async def _issue_token_pair(self, employee_id: str, employee_code: str, email: str) -> TokenPairResponse:
        access_token = self._token_service.create_access_token(employee_id, employee_code, email)
        refresh_token, expires_at = self._token_service.create_refresh_token(employee_id)
        token_hash = self._token_service.hash_token(refresh_token)
        await self._employee_persistence.store_refresh_token(employee_id, token_hash, expires_at)

        return TokenPairResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_minutes=self._token_service.access_token_expire_minutes,
        )
