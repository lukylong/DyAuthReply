#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: fu_user_query.py
@Desc: 
"""
from core.dept.dept_model import Dept
from core.user.user_model import User
from core.user.user_schema import UserSchemaGetNameIn


def get_manager_list(data: UserSchemaGetNameIn):
    level = data.level
    until = data.until
    manager_id_str = "manager__id"
    manager_name_str = "manager__name"
    if int(until) == 1:
        for i in range(int(level) - 1):
            manager_name_str = "manager__" + manager_name_str
            manager_id_str = "manager__" + manager_id_str
        user = User.objects.filter(id__in=data.ids).values(manager_id_str, manager_name_str)
        user = [{"manager_id": item.get(manager_id_str), "manager_name": item.get(manager_name_str)} for item in user]
        return user
    else:
        manager = User.objects.filter(id__in=data.ids).values(manager_id_str, manager_name_str)
        manager = [{"manager_id": item.get(manager_id_str), "manager_name": item.get(manager_name_str)} for
                   item in manager]
        manager_list = [manager]
        for i in range(int(until) - 1):
            manager_name_str = "manager__" + manager_name_str
            manager_id_str = "manager__" + manager_id_str
            manager_info = User.objects.filter(id__in=data.ids).values(manager_id_str, manager_name_str)
            manager_info = [{"manager_id": item.get(manager_id_str), "manager_name": item.get(manager_name_str)} for
                            item in manager_info]
            manager_list.append(manager_info)

        return manager_list


def get_dept_lead_list1(data: UserSchemaGetNameIn):
    level = data.level
    until = data.until
    dept_lead_id_str = "dept__lead__id"
    dept_lead_name_str = "dept__lead__name"
    if int(until) == 1:
        for i in range(int(level) - 1):
            dept_lead_name_str = "dept__lead__" + dept_lead_name_str
            dept_lead_id_str = "dept__lead__" + dept_lead_id_str
        user = User.objects.filter(id__in=data.ids).values(dept_lead_id_str, dept_lead_name_str)
        user = [{"dept_lead_id": item.get(dept_lead_id_str), "dept_lead_name": item.get(dept_lead_name_str)} for item in
                user]
        return user
    else:
        dept_lead = User.objects.filter(id__in=data.ids).values(dept_lead_id_str, dept_lead_name_str)
        dept_lead = [{"dept_lead_id": item.get(dept_lead_id_str), "dept_lead_name": item.get(dept_lead_name_str)} for
                     item in dept_lead]
        dept_lead_list = [dept_lead]
        for i in range(int(until) - 1):
            dept_lead_name_str = "dept__lead__" + dept_lead_name_str
            dept_lead_id_str = "dept__lead__" + dept_lead_id_str
            dept_lead_info = User.objects.filter(id__in=data.ids).values(dept_lead_id_str, dept_lead_name_str)
            dept_lead_info = [
                {"dept_lead_id": item.get(dept_lead_id_str), "dept_lead_name": item.get(dept_lead_name_str)} for
                item in dept_lead_info]
            dept_lead_list.append(dept_lead_info)

        return dept_lead_list


def get_dept_lead_list(data: UserSchemaGetNameIn):
    level = data.level
    until = data.until
    lead_id_str = "lead__id"
    lead_name_str = "lead__name"
    users_dept = User.objects.filter(id__in=data.ids).values("dept__id")
    dept_lead_list = []
    if int(until) == 1:
        for item in users_dept:
            dept_id = item.get("dept__id")
            for i in range(int(level) - 1):
                lead_name_str = "parent__" + lead_name_str
                lead_id_str = "parent__" + lead_id_str
            lead = Dept.objects.filter(id=dept_id).values(lead_id_str, lead_name_str)
            lead = [{"dept_lead_id": item.get(lead_id_str), "dept_lead_name": item.get(lead_name_str)} for item in lead]
            dept_lead_list.append(lead)
    else:
        lead_list = []
        for item in users_dept:
            dept_id = item.get("dept__id")
            lead = Dept.objects.filter(id=dept_id).values(lead_id_str, lead_name_str)
            lead = [{"dept_lead_id": item.get(lead_id_str), "dept_lead_name": item.get(lead_name_str)} for item in lead]
            lead_list.append(lead)

        dept_lead_list.append(lead_list)
        for i in range(int(until) - 1):
            lead_name_str = "parent__" + lead_name_str
            lead_id_str = "parent__" + lead_id_str
            lead_list = []
            for item in users_dept:
                dept_id = item.get("dept__id")
                lead = Dept.objects.filter(id=dept_id).values(lead_id_str, lead_name_str)
                lead = [{"dept_lead_id": item.get(lead_id_str), "dept_lead_name": item.get(lead_name_str)} for item in
                        lead]
                lead_list.append(lead)
            dept_lead_list.append([*lead_list])
    return dept_lead_list
