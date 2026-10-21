"""
Onsigning Standard Exception Hierarchy
Provides typed domain exceptions mapping to HTTP error codes and safe error JSON schemas.
"""

from typing import Optional, Any, Dict


class OnsigningException(Exception):
    """Base exception for all domain errors."""
    def __init__(self, message: str, error_category: str = "SYSTEM_ERROR", status_code: int = 500, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.error_category = error_category
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.error_category,
            "message": self.message,
            "status_code": self.status_code,
            "details": self.details
        }


class AuthenticationError(OnsigningException):
    """Raised when JWT verification fails or token is missing/expired."""
    def __init__(self, message: str = "Invalid or expired authentication credentials."):
        super().__init__(message=message, error_category="AUTHENTICATION_FAILED", status_code=401)


class AuthorizationError(OnsigningException):
    """Raised when an authenticated user lacks permissions or organization membership."""
    def __init__(self, message: str = "Access forbidden. User is not a member of the requested organization."):
        super().__init__(message=message, error_category="AUTHORIZATION_DENIED", status_code=403)


class TenantMismatchError(OnsigningException):
    """Raised when a cross-tenant resource access attempt is detected."""
    def __init__(self, message: str = "Resource does not belong to the authenticated organization."):
        super().__init__(message=message, error_category="TENANT_ISOLATION_VIOLATION", status_code=403)


class DocumentValidationError(OnsigningException):
    """Raised when an uploaded document violates size, format, page limit, or integrity constraints."""
    def __init__(self, message: str, error_category: str = "FILE_SIZE_EXCEEDED", status_code: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_category=error_category, status_code=status_code, details=details)


class ResourceNotFoundError(OnsigningException):
    """Raised when a requested entity cannot be found within the tenant context."""
    def __init__(self, resource: str, identifier: str):
        super().__init__(
            message=f"{resource} '{identifier}' not found in active workspace.",
            error_category="NOT_FOUND",
            status_code=404
        )


class PlaybookEvaluationError(OnsigningException):
    """Raised when playbook evaluation fails due to schema invalidity or missing rules."""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message=message, error_category="SCHEMA_VALIDATION_ERROR", status_code=422, details=details)
