#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: exe_sql.py
@Desc: 查询所有结果返回字典类型数据 - :param sql:
"""
from django.db import connection


def query_all_dict(sql, params=None):
    """
    查询所有结果返回字典类型数据
    :param sql:
    :param params:
    :return:
    """
    with connection.cursor() as cursor:
        if params:
            cursor.execute(sql, params=params)
        else:
            cursor.execute(sql)
        col_names = [desc[0] for desc in cursor.description]
        row = cursor.fetchall()
        row_list = []
        for l in row:
            t_map = dict(zip(col_names, l))
            row_list.append(t_map)
        return row_list
