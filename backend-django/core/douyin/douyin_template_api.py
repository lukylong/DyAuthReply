#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抖音回复模板 + 模板分类 API"""
import re
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.douyin.douyin_template_category_model import DouyinTemplateCategory
from core.douyin.douyin_template_model import DouyinTemplate
from core.douyin.douyin_template_schema import (
    DouyinTemplateBatchDeleteIn,
    DouyinTemplateCategoryFilters,
    DouyinTemplateCategorySchemaIn,
    DouyinTemplateCategorySchemaOut,
    DouyinTemplateFilters,
    DouyinTemplatePreviewIn,
    DouyinTemplatePreviewOut,
    DouyinTemplateSchemaIn,
    DouyinTemplateSchemaOut,
    DouyinTemplateSchemaPatch,
    DouyinTemplateSimpleOut,
)

router = Router()

# ============ 模板分类 ============

@router.get("/template-category", response=List[DouyinTemplateCategorySchemaOut], summary="模板分类列表（分页）")
@paginate(MyPagination)
def list_category(request, filters: DouyinTemplateCategoryFilters = Query(...)):
    return retrieve(request, DouyinTemplateCategory, filters)


@router.get("/template-category/tree", response=List[DouyinTemplateCategorySchemaOut], summary="模板分类树")
def category_tree(request):
    """返回两级分类树（parent 为空的根 + 各自 children）"""
    roots = list(DouyinTemplateCategory.objects.filter(parent__isnull=True))
    children_map: dict = {}
    for c in DouyinTemplateCategory.objects.filter(parent__isnull=False):
        children_map.setdefault(str(c.parent_id), []).append(c)
    for r in roots:
        r.resolved_children = children_map.get(str(r.id), [])
    result: List[DouyinTemplateCategorySchemaOut] = []
    for r in roots:
        out = DouyinTemplateCategorySchemaOut.from_orm(r).dict()
        out['children'] = [DouyinTemplateCategorySchemaOut.from_orm(c).dict() for c in getattr(r, 'resolved_children', [])]
        result.append(out)
    return result


@router.post("/template-category", response=DouyinTemplateCategorySchemaOut, summary="创建模板分类")
def create_category(request, data: DouyinTemplateCategorySchemaIn):
    return create(request, data.dict(), DouyinTemplateCategory)


@router.put("/template-category/{cat_id}", response=DouyinTemplateCategorySchemaOut, summary="更新模板分类")
def update_category(request, cat_id: str, data: DouyinTemplateCategorySchemaIn):
    cat = get_object_or_404(DouyinTemplateCategory, id=cat_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(cat, attr, value)
    cat.save()
    return cat


@router.delete("/template-category/{cat_id}", summary="删除模板分类")
def delete_category(request, cat_id: str):
    cat = get_object_or_404(DouyinTemplateCategory, id=cat_id)
    DouyinTemplate.objects.filter(category_id=cat_id).update(category=None)
    cat.delete()
    return {"success": True}


# ============ 模板 ============

@router.get("/template", response=List[DouyinTemplateSchemaOut], summary="模板列表（分页）")
@paginate(MyPagination)
def list_template(request, filters: DouyinTemplateFilters = Query(...)):
    return retrieve(request, DouyinTemplate, filters)


@router.get("/template/all", response=List[DouyinTemplateSimpleOut], summary="全部启用模板（下拉）")
def list_all_template(request):
    return DouyinTemplate.objects.filter(status=True, is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.post("/template/preview", response=DouyinTemplatePreviewOut, summary="模板预览（变量渲染）")
def preview_template(request, data: DouyinTemplatePreviewIn):
    tpl = get_object_or_404(DouyinTemplate, id=data.template_id)
    rendered = _render(tpl.content, data.context)
    return DouyinTemplatePreviewOut(rendered=rendered, links=tpl.links or [])


@router.get("/template/{tpl_id}", response=DouyinTemplateSchemaOut, summary="模板详情")
def get_template(request, tpl_id: str):
    return get_object_or_404(DouyinTemplate, id=tpl_id)


@router.post("/template", response=DouyinTemplateSchemaOut, summary="创建模板")
def create_template(request, data: DouyinTemplateSchemaIn):
    payload = data.dict()
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    # 自动推导 variables
    payload['variables'] = _extract_variables(payload.get('content', ''))
    return create(request, payload, DouyinTemplate)


@router.put("/template/{tpl_id}", response=DouyinTemplateSchemaOut, summary="更新模板")
def update_template(request, tpl_id: str, data: DouyinTemplateSchemaIn):
    tpl = get_object_or_404(DouyinTemplate, id=tpl_id)
    payload = data.dict(exclude_unset=True)
    if 'content' in payload:
        payload['variables'] = _extract_variables(payload['content'])
    for attr, value in payload.items():
        setattr(tpl, attr, value)
    tpl.save()
    return tpl


@router.patch("/template/{tpl_id}", response=DouyinTemplateSchemaOut, summary="部分更新模板")
def patch_template(request, tpl_id: str, data: DouyinTemplateSchemaPatch):
    tpl = get_object_or_404(DouyinTemplate, id=tpl_id)
    payload = data.dict(exclude_unset=True)
    if 'content' in payload:
        payload['variables'] = _extract_variables(payload['content'])
    for attr, value in payload.items():
        setattr(tpl, attr, value)
    tpl.save()
    return tpl


@router.delete("/template/{tpl_id}", summary="删除模板")
def delete_template(request, tpl_id: str):
    tpl = get_object_or_404(DouyinTemplate, id=tpl_id)
    tpl.delete()
    return {"success": True}


@router.post("/template/batch/delete", summary="批量删除模板")
def batch_delete_template(request, data: DouyinTemplateBatchDeleteIn):
    count = DouyinTemplate.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}


# ============ utils ============
_VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def _extract_variables(text: str) -> List[str]:
    """从 {{xxx}} 中提取变量名"""
    return sorted(set(_VAR_RE.findall(text or '')))


def _render(text: str, ctx: dict) -> str:
    """最简单的变量替换：未提供的变量保留原样"""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        return str(ctx.get(key, m.group(0)))

    return _VAR_RE.sub(repl, text or '')
