class RunnerError(Exception):
    """Base class for expected runner failures."""

    code = "runner_error"


class JobNotFound(RunnerError):
    code = "job_not_found"


class InvalidJobState(RunnerError):
    code = "invalid_job_state"


class SensitiveInputError(RunnerError):
    code = "sensitive_input"


class PolicyViolation(RunnerError):
    code = "policy_denied"


class AuditIntegrityError(RunnerError):
    code = "audit_integrity_error"
