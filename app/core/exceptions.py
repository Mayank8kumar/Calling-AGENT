"""
# Custom exception hierarchy — 15 exception classes
# Base: VoiceAgentError (message + code)
# Auth: AuthenticationError, AuthorizationError
# Tenant: TenantNotFoundError, TenantQuotaExceededError
# Call: CallError, CallNotFoundError, ConcurrentCallLimitError, CallTransferError
# Pipeline: PipelineError, STTError, LLMError, LLMTimeoutError, TTSError, TelephonyError
# Provider: ProviderUnavailableError, ProviderConfigError
# Compliance: ComplianceError, DNCViolationError, CallingHoursViolationError
"""
"""
Centralized exception hierarchy.
All platform exceptions inherit from VoiceAgentError for consistent handling.
"""

from __future__ import annotations


class VoiceAgentError(Exception):
    """Base exception for the entire platform."""

    def __init__(self, message: str = "An unexpected error occurred", code: str = "INTERNAL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


# --- Auth ---
class AuthenticationError(VoiceAgentError):
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, "AUTH_ERROR")


class AuthorizationError(VoiceAgentError):
    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message, "FORBIDDEN")


# --- Tenant ---
class TenantNotFoundError(VoiceAgentError):
    def __init__(self, tenant_id: str | None = None):
        msg = f"Tenant not found: {tenant_id}" if tenant_id else "Tenant not found"
        super().__init__(msg, "TENANT_NOT_FOUND")


class TenantQuotaExceededError(VoiceAgentError):
    def __init__(self, resource: str = "calls"):
        super().__init__(f"Tenant quota exceeded for {resource}", "QUOTA_EXCEEDED")


# --- Call ---
class CallError(VoiceAgentError):
    """Base for all call-related errors."""

    def __init__(self, message: str = "Call error", code: str = "CALL_ERROR"):
        super().__init__(message, code)


class CallNotFoundError(CallError):
    def __init__(self, call_id: str | None = None):
        msg = f"Call not found: {call_id}" if call_id else "Call not found"
        super().__init__(msg, "CALL_NOT_FOUND")


class ConcurrentCallLimitError(CallError):
    def __init__(self, limit: int):
        super().__init__(f"Concurrent call limit reached: {limit}", "CONCURRENT_LIMIT")


class CallTransferError(CallError):
    def __init__(self, message: str = "Call transfer failed"):
        super().__init__(message, "TRANSFER_ERROR")


# --- Voice Pipeline ---
class PipelineError(VoiceAgentError):
    """Base for AI pipeline errors."""

    def __init__(self, message: str = "Pipeline error", code: str = "PIPELINE_ERROR"):
        super().__init__(message, code)


class STTError(PipelineError):
    def __init__(self, provider: str = "unknown", message: str = "Speech-to-text failed"):
        super().__init__(f"[{provider}] {message}", "STT_ERROR")


class LLMError(PipelineError):
    def __init__(self, provider: str = "unknown", message: str = "LLM processing failed"):
        super().__init__(f"[{provider}] {message}", "LLM_ERROR")


class LLMTimeoutError(LLMError):
    def __init__(self, provider: str = "unknown", timeout_seconds: float = 0):
        super().__init__(provider, f"LLM timed out after {timeout_seconds}s")


class TTSError(PipelineError):
    def __init__(self, provider: str = "unknown", message: str = "Text-to-speech failed"):
        super().__init__(f"[{provider}] {message}", "TTS_ERROR")


class TelephonyError(PipelineError):
    def __init__(self, provider: str = "unknown", message: str = "Telephony error"):
        super().__init__(f"[{provider}] {message}", "TELEPHONY_ERROR")


# --- Provider ---
class ProviderUnavailableError(VoiceAgentError):
    def __init__(self, provider: str, stage: str):
        super().__init__(
            f"Provider '{provider}' unavailable for {stage}", "PROVIDER_UNAVAILABLE"
        )


class ProviderConfigError(VoiceAgentError):
    def __init__(self, provider: str, detail: str = ""):
        msg = f"Invalid configuration for provider '{provider}'"
        if detail:
            msg += f": {detail}"
        super().__init__(msg, "PROVIDER_CONFIG_ERROR")


# --- Compliance ---
class ComplianceError(VoiceAgentError):
    def __init__(self, message: str = "Compliance violation"):
        super().__init__(message, "COMPLIANCE_ERROR")


class DNCViolationError(ComplianceError):
    def __init__(self, phone_number: str = ""):
        msg = "Number is on Do-Not-Call registry"
        if phone_number:
            msg += f": {phone_number[-4:]}"  # Only last 4 digits for safety
        super().__init__(msg)


class CallingHoursViolationError(ComplianceError):
    def __init__(self, timezone: str = ""):
        super().__init__(f"Outside permitted calling hours (timezone: {timezone})")