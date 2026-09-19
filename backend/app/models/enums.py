from __future__ import annotations

import enum


class ProfileType(str, enum.Enum):
    """Export profile that drives Excel/PDF layout."""

    PROFILE_18233 = "18233"
    BEIJING = "BEIJING"


class ShipmentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    UPLOADING = "UPLOADING"
    PARSING = "PARSING"
    RECONCILING = "RECONCILING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY_TO_EXPORT = "READY_TO_EXPORT"
    EXPORTING = "EXPORTING"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


class DocType(str, enum.Enum):
    INVOICE = "INVOICE"
    PACKING_LIST = "PACKING_LIST"
    SPECIFICATION = "SPECIFICATION"
    CATALOG = "CATALOG"
    PERMIT = "PERMIT"


class ErrorType(str, enum.Enum):
    MISMATCH = "MISMATCH"
    MISSING_PAIR = "MISSING_PAIR"
    LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"
    CATALOG_NOT_FOUND = "CATALOG_NOT_FOUND"
    PERMIT_MULTIPLE_CANDIDATES = "PERMIT_MULTIPLE_CANDIDATES"
    SCAN_MISMATCH = "SCAN_MISMATCH"
    MODEL_CORRECTION = "MODEL_CORRECTION"


class ErrorSeverity(str, enum.Enum):
    """Maps 1:1 to operator workspace cell highlighting."""

    RED = "RED"  # value mismatches between files
    YELLOW = "YELLOW"  # missing / unlinked / catalog not found
    ORANGE = "ORANGE"  # low OCR confidence
    BLUE = "BLUE"  # multiple permit (RD) candidates


class PermitDatabaseSource(str, enum.Enum):
    DS_RD = "DS_RD"
    SS_RD = "SS_RD"
    SARMANT_RD = "SARMANT_RD"


class PermitStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    UNKNOWN = "UNKNOWN"
