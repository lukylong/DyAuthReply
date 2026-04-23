#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 1/24/2024 9:38 AM
# file: fu_crud.py
# author: 臧成龙
# QQ: 939589097
import uuid
from typing import Type
from urllib.parse import unquote

from django.db.models import Model, QuerySet
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from ninja import Schema
from openpyxl.reader.excel import load_workbook

from application.settings import BASE_DIR
from common.fu_schema import FuFilters
from common.utils.excel_utils import dict_to_excel


# from system.user.user_model import User
# from system.user.user_schema import UserSchemaGetNameIn


class ImportSchema(Schema):
    path: str


# bach create
def batch_create(request, list_data: list[dict], model: Type[Model]) -> int:
    list_subject = model.objects.bulk_create([model(**item) for item in list_data])
    count = len(list_subject)
    return count


def create(request, data: dict | Schema, model: Type[Model]) -> QuerySet:
    user_info = request.auth
    if not isinstance(data, dict):
        data = data.dict()
    data["sys_creator_id"] = user_info.id
    query_set = model.objects.create(**data)
    if isinstance(query_set.id, uuid.UUID):
        query_set.id = str(query_set.id)
    return query_set


def delete(id: str, model: Type[Model]) -> Type[Model]:
    instance = get_object_or_404(model, id=id)
    instance.delete()
    instance.id = id
    return instance


def batch_delete(ids: list[str], model: Type[Model]) -> int:
    count = model.objects.filter(id__in=ids).delete()[0]
    return count


def update(request, id: str, data: dict | Schema, model: Type[Model]) -> Type[Model]:
    user_info = request.auth
    if not isinstance(data, dict):
        data = data.dict(exclude_none=True)
    # data["sys_modifier"] = user_info.id
    instance = get_object_or_404(model, id=id)
    for attr, value in data.items():
        setattr(instance, attr, value)
    instance.save()
    return instance


def retrieve(request, model: Type[Model], filters: FuFilters = FuFilters()) -> QuerySet:
    query_set = model.objects.all()
    if filters is not None:
        # 将filters空字符串转换为None
        for attr, value in filters.dict().items():
            if getattr(filters, attr) == '':
                setattr(filters, attr, None)
        query_set = filters.filter(query_set)
    return query_set


def get_or_none(model: Type[Model], *args, **kwargs):
    try:
        return model.objects.get(*args, **kwargs)
    except model.DoesNotExist:
        return None


def export_data(request, model, scheme, export_fields):
    """
    导出数据为Excel文件。

    参数:
    - request: HttpRequest对象，表示客户端请求。
    - model: Django模型类，指定要导出数据的模型。
    - scheme: 表示数据转换规则的对象，用于将ORM对象转换为字典。
    - export_fields: 包含要导出的字段名的列表。

    返回值:
    - FileResponse对象，提供下载Excel文件。
    """

    title_dict = {}
    # 根据export_fields列表获取字段的显示名称
    for field in export_fields:
        field_obj = getattr(model, field).field
        title_dict[field] = field_obj.help_text

    qs = retrieve(request, model)
    list_data = []
    # 将查询集中的每一项转换为指定格式的字典
    for qs_item in qs:
        qs_item = scheme.from_orm(qs_item)
        dict_data = {}
        for item, value in title_dict.items():
            dict_data[value] = getattr(qs_item, item)
        list_data.append(dict_data)

    file_url = dict_to_excel(list_data)
    # 返回供下载的文件响应
    return FileResponse(open(file_url, "rb"), as_attachment=True)


def import_data(request, model, scheme, data, import_fields):
    """
    导入数据到指定模型

    参数:
    - request: HttpRequest对象，表示客户端请求
    - model: Django模型类，数据将被导入到这个模型
    - scheme: 一个函数，用于根据给定的数据字典创建模型实例
    - data: 包含要导入文件信息的对象，比如上传的Excel文件
    - import_fields: 一个列表，指定模型中需要导入的字段名

    返回值:
    - FuResponse对象，包含导入结果的消息
    """
    title_dict = {}  # 字段名与Excel列对应的字典
    for field in import_fields:
        field_obj = getattr(model, field).field
        title_dict[field_obj.help_text] = field_obj.column
    # 文件路径处理
    file_path = str(BASE_DIR) + '/' + unquote(data.path)
    # 加载Excel工作簿
    wb = load_workbook(file_path)
    ws = wb.active  # 获取活动工作表
    title_value = []
    for index_row, row in enumerate(ws.values):
        if index_row == 0:
            title_value = row  # 读取Excel表头
        else:
            dict_data = {}  # 存储每一行数据转换后的字典
            for index, cell in enumerate(row):
                title_cell = title_value[index]
                value = title_dict.get(title_cell)  # 根据表头查找对应字段
                if value is not None:
                    dict_data[value] = cell
            print(dict_data)  # 打印处理后的数据，用于调试
            data = scheme(**dict_data)  # 根据处理后的字典创建模型实例
            create(request, data, model)  # 在数据库中创建模型实例
    return {"msg": "导入成功"}  # 返回成功消息
