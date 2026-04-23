#!/usr/bin/env python
# -*- coding: utf-8 -*-
# time: 2024/12/19
# file: file_manager_schema.py
# author: 臧成龙
# QQ: 939589097

from typing import List

from ninja import ModelSchema, Schema, Field
from pydantic import UUID4

from common.fu_model import exclude_fields
from common.fu_schema import FuFilters
from core.file_manager.file_manager_model import FileManager


class FileManagerFilters(FuFilters):
    """文件管理过滤器"""
    name: str = Field(None, q="name__icontains", alias="name")
    type: str = Field(None, alias="type")
    parent_id: UUID4 = Field(None, alias="parent_id")
    storage_type: str = Field(None, alias="storage_type")
    file_ext: str = Field(None, alias="file_ext")
    is_public: bool = Field(None, alias="is_public")


class FileManagerSchemaIn(ModelSchema):
    """文件管理输入Schema"""
    parent_id: UUID4 | None = Field(None, alias="parent_id")

    class Config:
        model = FileManager
        model_exclude = exclude_fields + ('parent', 'url', 'thumbnail_url', 'download_count')


class FileManagerSimpleSchemaOut(Schema):
    """文件管理简单输出Schema"""
    id: UUID4 = Field(...)
    name: str = Field(...)
    type: str = Field(...)
    size: int = Field(None)


class FileManagerSchemaOut(ModelSchema):
    """文件管理输出Schema"""
    parent_id: UUID4 | None = Field(None)
    id: UUID4 = Field(...)
    parent_name: str | None = Field(None)
    has_children: bool = Field(False)
    file_type: str = Field(None)
    file_size: int = Field(None)
    updated_time: str = Field(None)

    class Config:
        model = FileManager
        model_exclude = ['parent', 'type', 'size', 'sys_create_datetime', 'sys_update_datetime']

    @staticmethod
    def resolve_parent_id(obj):
        return obj.parent_id

    @staticmethod
    def resolve_parent_name(obj):
        return obj.parent.name if obj.parent else None

    @staticmethod
    def resolve_has_children(obj):
        return obj.children.exists() if obj.type == 'folder' else False

    @staticmethod
    def resolve_file_type(obj):
        return obj.type

    @staticmethod
    def resolve_file_size(obj):
        return obj.size

    @staticmethod
    def resolve_updated_time(obj):
        return obj.sys_update_datetime.isoformat() if obj.sys_update_datetime else obj.sys_create_datetime.isoformat()


class CreateFolderSchemaIn(Schema):
    """创建文件夹输入Schema"""
    name: str = Field(..., description="文件夹名称")
    parent_id: UUID4 | None = Field(None, description="父文件夹ID")


class MoveItemsSchemaIn(Schema):
    """移动文件/文件夹输入Schema"""
    ids: List[UUID4] = Field(..., description="要移动的文件/文件夹ID列表")
    target_folder_id: UUID4 | None = Field(None, description="目标文件夹ID")


class RenameItemSchemaIn(Schema):
    """重命名输入Schema"""
    name: str = Field(..., description="新名称")


class BatchDeleteSchemaIn(Schema):
    """批量删除输入Schema"""
    ids: List[UUID4] = Field(..., description="要删除的文件/文件夹ID列表")


class FileStorageConfigSchema(Schema):
    """文件存储配置Schema"""
    storage_type: str = Field('local', description="存储类型")
    local_base_path: str | None = Field(None, description="本地存储路径")
    oss_endpoint: str | None = Field(None, description="OSS端点")
    oss_access_key_id: str | None = Field(None, description="OSS访问密钥ID")
    oss_access_key_secret: str | None = Field(None, description="OSS访问密钥")
    oss_bucket_name: str | None = Field(None, description="OSS存储桶名称")
    minio_endpoint: str | None = Field(None, description="Minio端点")
    minio_access_key: str | None = Field(None, description="Minio访问密钥")
    minio_secret_key: str | None = Field(None, description="Minio密钥")
    minio_bucket_name: str | None = Field(None, description="Minio存储桶名称")
    azure_account_name: str | None = Field(None, description="Azure存储账户名称")
    azure_account_key: str | None = Field(None, description="Azure存储账户密钥")
    azure_container_name: str | None = Field(None, description="Azure容器名称")

# ==================== 分块上传相关 Schema ====================


class InitChunkUploadSchemaIn(Schema):
    """初始化分块上传输入Schema"""
    filename: str = Field(..., description="文件名")
    total_size: int = Field(..., description="文件总大小（字节）")
    chunk_size: int = Field(5 * 1024 * 1024, description="分块大小（字节），默认5MB")
    parent_id: UUID4 | None = Field(None, description="父文件夹ID")
    is_public: bool = Field(False, description="是否公开")
    file_hash: str | None = Field(None, description="文件MD5哈希，用于秒传")


class InitChunkUploadSchemaOut(Schema):
    """初始化分块上传输出Schema"""
    upload_id: str = Field(..., description="上传ID")
    chunk_size: int = Field(..., description="分块大小")
    total_chunks: int = Field(..., description="总分块数")
    uploaded_chunks: List[int] = Field([], description="已上传的分块索引列表")
    file_exists: bool = Field(False, description="文件是否已存在（秒传）")
    file_id: UUID4 | None = Field(None, description="如果文件已存在，返回文件ID")


class UploadChunkSchemaOut(Schema):
    """上传分块输出Schema"""
    chunk_index: int = Field(..., description="分块索引")
    uploaded: bool = Field(..., description="是否上传成功")


class MergeChunksSchemaIn(Schema):
    """合并分块输入Schema"""
    upload_id: str = Field(..., description="上传ID")


class ChunkUploadStatusSchemaOut(Schema):
    """分块上传状态输出Schema"""
    upload_id: str = Field(..., description="上传ID")
    filename: str = Field(..., description="文件名")
    total_size: int = Field(..., description="文件总大小")
    total_chunks: int = Field(..., description="总分块数")
    uploaded_chunks: List[int] = Field(..., description="已上传的分块索引")
    completed: bool = Field(..., description="是否完成上传")
