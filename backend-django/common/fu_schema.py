#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 1/24/2024 10:20 AM
# file: fu_schema.py
# author: 臧成龙
# QQ: 939589097

from ninja import Schema, FilterSchema, Field


class FuFilters(FilterSchema):
    creator_id: str = Field(None, alias="creator_id")
    curr_flag: bool = Field(None, alias="curr_flag")


class UserSchema(Schema):
    id: str = None
    name: str = None


def response_success(data='success'):
    return {"detail": data}
