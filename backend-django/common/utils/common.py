#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: 臧成龙
@Contact: 939589097@qq.com
@Time: 2025-12-31
@File: common.py
@Desc: 对数组进行分页处理 - 
"""
def paginate_array(data_array, page=1, page_size=10):
    """
    对数组进行分页处理

    参数:
        data_array: 要分页的原始数组
        page: 页码，默认为1（从1开始）
        page_size: 每页显示的数量，默认为10

    返回:
        分页后的数组
    """
    # 验证输入
    if not isinstance(data_array, list):
        raise ValueError("输入必须是一个数组")

    if page < 1:
        page = 1

    if page_size < 1:
        page_size = 10

    # 计算起始索引和结束索引
    start_index = (page - 1) * page_size
    end_index = start_index + page_size

    # 返回分页后的数据
    return {"items": data_array[start_index:end_index], "total": len(data_array)}


def find_min_max_objects(objects, property_name):
    """
    查找数组中指定属性值最大和最小的对象

    参数:
        objects: 包含对象的数组
        property_name: 要比较的属性名称

    返回:
        包含最小对象和最大对象的元组 (min_object, max_object)
    """
    if not objects:
        return (None, None)

    # 初始化最小和最大对象为第一个元素
    min_obj = max_obj = objects[0].dict()

    for obj in objects[1:]:
        # 检查对象是否包含指定属性
        obj = obj.dict()
        if property_name not in obj:
            raise ValueError(f"对象缺少属性: {property_name}")

        # 比较属性值并更新最小/最大对象
        if obj[property_name] < min_obj[property_name]:
            min_obj = obj
        if obj[property_name] > max_obj[property_name]:
            max_obj = obj

    return {"min_obj": min_obj, "max_obj": max_obj}


def format_decimal_with_commas(decimal_num):
    # 转换为字符串并按小数点分割
    str_num = str(decimal_num)
    if '.' in str_num:
        integer_part, decimal_part = str_num.split('.')
        # 处理整数部分的千分位
        integer_part = '{:,}'.format(int(integer_part))
        return f"{integer_part}.{decimal_part}"
    else:
        # 没有小数部分的情况
        return '{:,}'.format(int(str_num))
