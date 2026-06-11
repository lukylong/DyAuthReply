#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跨平台回复模板 + 模板分类 API"""
import re
from typing import List

from django.shortcuts import get_object_or_404
from ninja import Query, Router
from ninja.errors import HttpError
from ninja.pagination import paginate

from common.fu_crud import create, retrieve
from common.fu_pagination import MyPagination
from core.social.constants import PLATFORM_VALUES
from core.social.template_category_model import ReplyTemplateCategory
from core.social.template_category_schema import (
    ReplyTemplateCategoryBatchDeleteIn,
    ReplyTemplateCategoryFilters,
    ReplyTemplateCategorySchemaIn,
    ReplyTemplateCategorySchemaOut,
)
from core.social.template_model import ReplyTemplate
from core.social.template_schema import (
    ReplyTemplateBatchDeleteIn,
    ReplyTemplateFilters,
    ReplyTemplatePreviewIn,
    ReplyTemplatePreviewOut,
    ReplyTemplateSchemaIn,
    ReplyTemplateSchemaOut,
    ReplyTemplateSchemaPatch,
    ReplyTemplateSimpleOut,
)

router = Router()


def _check_platform(platform) -> None:
    if platform and platform not in PLATFORM_VALUES:
        raise HttpError(400, f"不支持的平台: {platform}")


# ============ 模板分类 ============

@router.get("/template-category", response=List[ReplyTemplateCategorySchemaOut], summary="模板分类列表（分页）")
@paginate(MyPagination)
def list_category(request, filters: ReplyTemplateCategoryFilters = Query(...)):
    return retrieve(request, ReplyTemplateCategory, filters)


@router.get("/template-category/tree", response=List[ReplyTemplateCategorySchemaOut], summary="模板分类树")
def category_tree(request):
    """返回两级分类树（parent 为空的根 + 各自 children）"""
    roots = list(ReplyTemplateCategory.objects.filter(parent__isnull=True))
    children_map: dict = {}
    for c in ReplyTemplateCategory.objects.filter(parent__isnull=False):
        children_map.setdefault(str(c.parent_id), []).append(c)
    result: List[dict] = []
    for r in roots:
        out = ReplyTemplateCategorySchemaOut.from_orm(r).dict()
        out['children'] = [
            ReplyTemplateCategorySchemaOut.from_orm(c).dict()
            for c in children_map.get(str(r.id), [])
        ]
        result.append(out)
    return result


@router.post("/template-category", response=ReplyTemplateCategorySchemaOut, summary="创建模板分类")
def create_category(request, data: ReplyTemplateCategorySchemaIn):
    return create(request, data.dict(), ReplyTemplateCategory)


@router.put("/template-category/{cat_id}", response=ReplyTemplateCategorySchemaOut, summary="更新模板分类")
def update_category(request, cat_id: str, data: ReplyTemplateCategorySchemaIn):
    cat = get_object_or_404(ReplyTemplateCategory, id=cat_id)
    for attr, value in data.dict(exclude_unset=True).items():
        setattr(cat, attr, value)
    cat.save()
    return cat


@router.delete("/template-category/{cat_id}", summary="删除模板分类")
def delete_category(request, cat_id: str):
    cat = get_object_or_404(ReplyTemplateCategory, id=cat_id)
    ReplyTemplate.objects.filter(category_id=cat_id).update(category=None)
    cat.delete()
    return {"success": True}


@router.post("/template-category/batch/delete", summary="批量删除模板分类")
def batch_delete_category(request, data: ReplyTemplateCategoryBatchDeleteIn):
    ReplyTemplate.objects.filter(category_id__in=data.ids).update(category=None)
    count = ReplyTemplateCategory.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}


# ============ 模板 ============

@router.get("/template", response=List[ReplyTemplateSchemaOut], summary="模板列表（分页）")
@paginate(MyPagination)
def list_template(request, filters: ReplyTemplateFilters = Query(...)):
    return retrieve(request, ReplyTemplate, filters)


@router.get("/template/all", response=List[ReplyTemplateSimpleOut], summary="全部启用模板（下拉）")
def list_all_template(request):
    return ReplyTemplate.objects.filter(status=True, is_deleted=False).order_by('-sort', '-sys_create_datetime')


@router.post("/template/preview", response=ReplyTemplatePreviewOut, summary="模板预览（变量渲染）")
def preview_template(request, data: ReplyTemplatePreviewIn):
    tpl = get_object_or_404(ReplyTemplate, id=data.template_id)
    rendered = _render(tpl.content, data.context)
    return ReplyTemplatePreviewOut(rendered=rendered, links=tpl.links or [])


@router.get("/template/{tpl_id}", response=ReplyTemplateSchemaOut, summary="模板详情")
def get_template(request, tpl_id: str):
    return get_object_or_404(ReplyTemplate, id=tpl_id)


@router.post("/template", response=ReplyTemplateSchemaOut, summary="创建模板")
def create_template(request, data: ReplyTemplateSchemaIn):
    payload = data.dict()
    _check_platform(payload.get('platform'))
    if not payload.get('owner_id'):
        payload['owner_id'] = request.auth.id
    payload['variables'] = _extract_variables(payload.get('content', ''))
    return create(request, payload, ReplyTemplate)


@router.put("/template/{tpl_id}", response=ReplyTemplateSchemaOut, summary="更新模板")
def update_template(request, tpl_id: str, data: ReplyTemplateSchemaIn):
    tpl = get_object_or_404(ReplyTemplate, id=tpl_id)
    payload = data.dict(exclude_unset=True)
    _check_platform(payload.get('platform'))
    if 'content' in payload:
        payload['variables'] = _extract_variables(payload['content'])
    for attr, value in payload.items():
        setattr(tpl, attr, value)
    tpl.save()
    return tpl


@router.patch("/template/{tpl_id}", response=ReplyTemplateSchemaOut, summary="部分更新模板")
def patch_template(request, tpl_id: str, data: ReplyTemplateSchemaPatch):
    tpl = get_object_or_404(ReplyTemplate, id=tpl_id)
    payload = data.dict(exclude_unset=True)
    _check_platform(payload.get('platform'))
    if 'content' in payload:
        payload['variables'] = _extract_variables(payload['content'])
    for attr, value in payload.items():
        setattr(tpl, attr, value)
    tpl.save()
    return tpl


@router.delete("/template/{tpl_id}", summary="删除模板")
def delete_template(request, tpl_id: str):
    tpl = get_object_or_404(ReplyTemplate, id=tpl_id)
    tpl.delete()
    return {"success": True}


@router.post("/template/batch/delete", summary="批量删除模板")
def batch_delete_template(request, data: ReplyTemplateBatchDeleteIn):
    count = ReplyTemplate.objects.filter(id__in=data.ids).delete()[0]
    return {"count": count}


# ============ utils ============
_VAR_RE = re.compile(r'\{\{\s*(\w+)\s*\}\}')


def _extract_variables(text: str) -> List[str]:
    """从 {{xxx}} 中提取变量名"""
    return sorted(set(_VAR_RE.findall(text or '')))


def _render(text: str, ctx: dict) -> str:
    """最简单的变量替换：未提供的变量保留原样"""
    def repl(m: "re.Match") -> str:
        key = m.group(1)
        return str(ctx.get(key, m.group(0)))

    return _VAR_RE.sub(repl, text or '')
