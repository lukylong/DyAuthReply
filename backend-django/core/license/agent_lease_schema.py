"""Versioned, bounded control-plane input. No platform cookies or message bodies."""
from typing import Literal
from uuid import UUID
from ninja import Schema
from pydantic import Field, model_validator

MAX_EPOCH = 2**63 - 1


class AgentAccountOperation(Schema):
    platform: Literal['douyin'] = 'douyin'
    platform_account_id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_.:-]+$')
    local_account_id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_.:-]+$')
    action: Literal['acquire', 'renew', 'release']
    expected_epoch: int = Field(default=0, ge=0, le=MAX_EPOCH)

    @model_validator(mode='after')
    def validate_epoch(self):
        if self.action == 'acquire' and self.expected_epoch != 0:
            raise ValueError('acquire requires expected_epoch=0')
        if self.action != 'acquire' and self.expected_epoch == 0:
            raise ValueError('renew/release require an existing epoch')
        return self


class AgentLeaseSyncIn(Schema):
    activation_id: UUID
    activation_token: str = Field(min_length=1, max_length=512, repr=False)
    instance_id: UUID
    boot_id: UUID
    request_id: UUID
    sequence: int = Field(ge=1, le=MAX_EPOCH)
    accounts: list[AgentAccountOperation] = Field(min_length=1, max_length=300)

    @model_validator(mode='after')
    def unique_accounts(self):
        if any(value.int == 0 for value in (self.activation_id, self.instance_id, self.boot_id, self.request_id)):
            raise ValueError('nil owner/request UUID is not accepted')
        identities = [(a.platform, a.platform_account_id) for a in self.accounts]
        local = [a.local_account_id for a in self.accounts]
        if len(set(identities)) != len(identities) or len(set(local)) != len(local):
            raise ValueError('duplicate platform/local account in batch')
        return self


class AgentLeaseSyncOut(Schema):
    token: str
