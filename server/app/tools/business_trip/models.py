"""Schemas for a business trip built around fixed bookings and meetings."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


CommitmentKind = Literal["flight", "hotel", "meeting", "transport", "other"]
PlanItemKind = Literal["fixed", "recommended", "transfer", "preparation"]
ReceiptCategory = Literal["flight", "hotel", "taxi", "transport", "meal", "other"]


def build_business_trip_gmail_query(
    destination: str,
    start_date: date,
    end_date: date,
) -> str:
    """Build the immutable Gmail query from a user's natural-language trip scope."""
    safe_destination = " ".join(
        "".join(
            character if character.isalnum() or character in " -_" else " "
            for character in destination
        ).split()
    )
    if not safe_destination:
        raise ValueError("destination must contain searchable text")
    received_after = start_date - timedelta(days=180)
    received_before = end_date + timedelta(days=8)
    return (
        f"after:{received_after:%Y/%m/%d} "
        f"before:{received_before:%Y/%m/%d} "
        f'"{safe_destination}" '
        "{机票 航班 flight 酒店 hotel 会议 meeting 预订 booking 行程 itinerary}"
    )


class TripCommitment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    kind: CommitmentKind
    title: str = Field(min_length=1, max_length=300)
    start_at: AwareDatetime = Field(alias="startAt")
    end_at: AwareDatetime = Field(alias="endAt")
    location: str | None = Field(default=None, max_length=500)
    confirmation_code: str | None = Field(
        default=None, alias="confirmationCode", max_length=200
    )
    source_label: str | None = Field(default=None, alias="sourceLabel", max_length=300)
    source_message_id: str | None = Field(
        default=None, alias="sourceMessageId", max_length=200
    )
    notes: str | None = Field(default=None, max_length=2_000)

    @field_validator("id", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Text must not be empty")
        return stripped

    @field_validator(
        "location", "confirmation_code", "source_label", "source_message_id", "notes"
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_interval(self) -> "TripCommitment":
        if self.end_at <= self.start_at:
            raise ValueError("endAt must be after startAt")
        return self


class TripReceipt(BaseModel):
    """Structured evidence extracted only from a user-selected receipt."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    category: ReceiptCategory
    merchant: str = Field(min_length=1, max_length=300)
    transaction_date: date = Field(alias="transactionDate")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    invoice_number: str | None = Field(
        default=None, alias="invoiceNumber", max_length=200
    )
    order_number: str | None = Field(
        default=None, alias="orderNumber", max_length=200
    )
    source_label: str = Field(alias="sourceLabel", min_length=1, max_length=300)
    notes: str | None = Field(default=None, max_length=1_000)

    @field_validator("id", "merchant", "source_label")
    @classmethod
    def strip_receipt_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Text must not be empty")
        return stripped

    @field_validator("invoice_number", "order_number", "notes")
    @classmethod
    def strip_receipt_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


class BusinessTripInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    destination: str = Field(min_length=1, max_length=200)
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    time_zone: str = Field(alias="timeZone", min_length=1, max_length=100)
    commitments: list[TripCommitment] = Field(default_factory=list, max_length=30)
    receipts: list[TripReceipt] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Receipts explicitly observed in user-selected images or files. Never invent "
            "a receipt or amount; sourceLabel must identify the selected evidence."
        ),
    )
    search_gmail: bool = Field(
        default=False,
        alias="searchGmail",
        description=(
            "True only when the user explicitly asks WellPhone to search Gmail for "
            "trip evidence. The server constructs the Gmail query."
        ),
    )
    gmail_query: str | None = Field(
        default=None,
        alias="gmailQuery",
        max_length=500,
        description=(
            "Backward-compatible authorization marker. Do not construct Gmail search "
            "operators; prefer searchGmail. The server replaces this value with a "
            "deterministic query derived from the trip scope."
        ),
    )
    preferences: list[str] = Field(default_factory=list, max_length=12)
    notes: str | None = Field(default=None, max_length=2_000)
    check_calendar: bool = Field(
        default=False,
        alias="checkCalendar",
        description=(
            "True only when the user explicitly asks WellPhone to read existing "
            "calendar events and check them for conflicts with the trip."
        ),
    )
    add_to_calendar: bool = Field(default=False, alias="addToCalendar")
    add_calendar_alerts: bool = Field(default=False, alias="addCalendarAlerts")
    upload_to_drive: bool = Field(default=False, alias="uploadToDrive")
    drive_folder_id: str | None = Field(
        default=None, alias="driveFolderId", max_length=200
    )

    @field_validator("destination")
    @classmethod
    def strip_destination(cls, value: str) -> str:
        return value.strip()

    @field_validator("gmail_query", "drive_folder_id")
    @classmethod
    def strip_optional_input_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_trip(self) -> "BusinessTripInput":
        duration = (self.end_date - self.start_date).days + 1
        if duration < 1 or duration > 14:
            raise ValueError("Business trips must be between 1 and 14 days")
        try:
            ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("timeZone must be a valid IANA identifier") from error
        if self.gmail_query is not None:
            self.search_gmail = True
        if self.search_gmail:
            self.gmail_query = build_business_trip_gmail_query(
                self.destination,
                self.start_date,
                self.end_date,
            )
        if not self.commitments and not self.search_gmail:
            raise ValueError("at least one commitment or searchGmail is required")
        ids = [item.id for item in self.commitments]
        if len(ids) != len(set(ids)):
            raise ValueError("commitment ids must be unique")
        receipt_ids = [item.id for item in self.receipts]
        if len(receipt_ids) != len(set(receipt_ids)):
            raise ValueError("receipt ids must be unique")
        zone = ZoneInfo(self.time_zone)
        for commitment in self.commitments:
            local_start_date = commitment.start_at.astimezone(zone).date()
            if not self.start_date <= local_start_date <= self.end_date:
                raise ValueError(
                    f"commitment {commitment.id} starts outside the trip dates"
                )
        if self.add_calendar_alerts and not self.add_to_calendar:
            raise ValueError("addCalendarAlerts requires addToCalendar")
        if self.drive_folder_id and not self.upload_to_drive:
            raise ValueError("driveFolderId requires uploadToDrive")
        return self


class GmailSearchArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_results: int = Field(default=12, alias="maxResults", ge=1, le=20)


class CalendarEventsSearchArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    max_results: int = Field(default=100, alias="maxResults", ge=1, le=200)


class CalendarEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    start_at: AwareDatetime = Field(alias="startAt")
    end_at: AwareDatetime = Field(alias="endAt")
    location: str | None = Field(default=None, max_length=500)
    calendar_title: str | None = Field(
        default=None, alias="calendarTitle", max_length=300
    )
    is_all_day: bool = Field(default=False, alias="isAllDay")

    @field_validator("id", "title")
    @classmethod
    def strip_calendar_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Text must not be empty")
        return stripped

    @field_validator("location", "calendar_title")
    @classmethod
    def strip_calendar_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_calendar_interval(self) -> "CalendarEvent":
        if self.end_at <= self.start_at:
            raise ValueError("endAt must be after startAt")
        return self


class GmailMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    thread_id: str | None = Field(default=None, alias="threadId", max_length=200)
    subject: str = Field(default="（无主题）", max_length=500)
    sender: str | None = Field(default=None, max_length=500)
    date: str | None = Field(default=None, max_length=200)
    snippet: str = Field(default="", max_length=2_000)
    body_text: str = Field(default="", alias="bodyText", max_length=16_000)


class BusinessTripCommitmentsLockArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    commitments: list[TripCommitment] = Field(min_length=1, max_length=30)


class BusinessTripPlanItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: PlanItemKind
    name: str = Field(min_length=1, max_length=300)
    start_at: AwareDatetime = Field(alias="startAt")
    end_at: AwareDatetime = Field(alias="endAt")
    source_commitment_id: str | None = Field(
        default=None, alias="sourceCommitmentId", max_length=100
    )
    place_name: str | None = Field(default=None, alias="placeName", max_length=300)
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2_000)
    route_id: str | None = Field(default=None, alias="routeId", max_length=500)
    origin_place_name: str | None = Field(
        default=None, alias="originPlaceName", max_length=300
    )
    destination_place_name: str | None = Field(
        default=None, alias="destinationPlaceName", max_length=300
    )

    @model_validator(mode="after")
    def validate_interval(self) -> "BusinessTripPlanItem":
        if self.end_at <= self.start_at:
            raise ValueError("endAt must be after startAt")
        if self.kind == "fixed" and self.source_commitment_id is None:
            raise ValueError("fixed items require sourceCommitmentId")
        if self.kind != "fixed" and self.source_commitment_id is not None:
            raise ValueError("only fixed items may reference a commitment")
        if self.kind == "recommended" and self.place_name is None:
            raise ValueError("recommended items require placeName")
        return self


class BusinessTripPlanDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    theme: str = Field(min_length=1, max_length=200)
    items: list[BusinessTripPlanItem] = Field(min_length=1, max_length=16)


class BusinessTripPlanOutput(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    overview: str = Field(min_length=1, max_length=4_000)
    time_zone: str = Field(alias="timeZone", min_length=1, max_length=100)
    days: list[BusinessTripPlanDay] = Field(min_length=1, max_length=14)
    checklist: list[str] = Field(default_factory=list, max_length=30)


class BusinessTripSubmitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: BusinessTripPlanOutput
