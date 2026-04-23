import { useAccessStore } from '@vben/stores';

/**
 * -*- coding: utf-8 -*-
 * time: 2024/12/19
 * author: 臧成龙
 * QQ: 939589097
 */
import { requestClient } from '#/api/request';

export namespace SystemFileManagerApi {
  export interface FileItem {
    [key: string]: any;
    id?: string;
    name: string;
    path: string;
    file_type: 'file' | 'folder'; // 后端返回 file_type
    file_size?: number; // 后端返回 file_size
    file_ext?: string;
    parent_id?: string;
    updated_time?: string; // 后端返回 updated_time
    has_children?: boolean;
  }

  export interface FolderTree {
    id: string;
    name: string;
    path: string;
    parent_id?: string;
    children?: FolderTree[];
  }

  export interface FileListParams {
    path?: string;
    parent_id?: null | string;
    type?: 'file' | 'folder';
    page?: number;
    pageSize?: number;
  }

  export interface FileListResult {
    items: FileItem[];
    total: number;
  }

  export interface StorageConfig {
    [key: string]: any;
    storage_type?: string;
    max_file_size?: number;
    allowed_extensions?: string[];
  }

  export interface MoveParams {
    source_ids: string[];
    target_parent_id: string;
  }

  export interface RenameParams {
    name: string;
  }

  export interface BatchDeleteParams {
    ids: string[];
  }

  export interface FolderCreateParams {
    name: string;
    parent_id?: string;
    path?: string;
  }
}

/**
 * 获取文件列表
 * @param params 查询参数
 */
async function getFileList(params?: SystemFileManagerApi.FileListParams) {
  return requestClient.get<SystemFileManagerApi.FileListResult>(
    '/api/core/file_manager',
    { params },
  );
}

/**
 * 上传文件
 * @param file 文件对象
 * @param parentId 父文件夹ID
 * @param onProgress 上传进度回调
 */
async function uploadFile(
  file: File,
  parentId?: string | undefined,
  onProgress?: (progressEvent: {
    loaded: number;
    percentage: number;
    total: number;
  }) => void,
) {
  return requestClient.upload(
    '/api/core/file_manager/upload',
    {
      file,
      parent_id: parentId || undefined,
    },
    {
      onUploadProgress: (progressEvent: any) => {
        if (onProgress && progressEvent.total) {
          const percentage = Math.round(
            (progressEvent.loaded * 100) / progressEvent.total,
          );
          onProgress({
            loaded: progressEvent.loaded,
            total: progressEvent.total,
            percentage,
          });
        }
      },
    },
  );
}

/**
 * 创建文件夹
 * @param data 文件夹数据
 */
async function createFolder(data: SystemFileManagerApi.FolderCreateParams) {
  return requestClient.post('/api/core/file_manager/folder', data);
}

/**
 * 获取文件夹树
 */
async function getFolderTree() {
  return requestClient.get<Array<SystemFileManagerApi.FolderTree>>(
    '/api/core/file_manager/tree',
  );
}

/**
 * 重命名文件/文件夹
 * @param id 文件/文件夹ID
 * @param data 重命名数据
 */
async function renameItem(id: string, data: SystemFileManagerApi.RenameParams) {
  return requestClient.put(`/api/core/file_manager/${id}/rename`, data);
}

/**
 * 移动文件/文件夹
 * @param data 移动参数
 */
async function moveItems(data: SystemFileManagerApi.MoveParams) {
  return requestClient.put('/api/core/file_manager/move', data);
}

/**
 * 删除文件/文件夹
 * @param id 文件/文件夹ID
 */
async function deleteItem(id: string) {
  return requestClient.delete(`/api/core/file_manager/${id}`);
}

/**
 * 批量删除文件/文件夹
 * @param data 批量删除参数
 */
async function batchDelete(data: SystemFileManagerApi.BatchDeleteParams) {
  return requestClient.post('/api/core/file_manager/batch/delete', data);
}

/**
 * 获取下载链接
 * @param path 文件路径
 */
function getDownloadUrl(path: string): string {
  return `/basic-api/api/core/file_manager/file/download?path=${encodeURIComponent(path)}`;
}

/**
 * 获取存储配置
 */
async function getStorageConfig() {
  return requestClient.get<SystemFileManagerApi.StorageConfig>(
    '/api/core/file_manager/storage_config',
  );
}

/**
 * 更新存储配置
 * @param data 存储配置数据
 */
async function updateStorageConfig(data: SystemFileManagerApi.StorageConfig) {
  return requestClient.put('/api/core/file_manager/storage_config', data);
}

/**
 * 通过文件ID获取文件访问URL
 * @param fileId 文件ID
 */
async function getFileUrlById(fileId: string) {
  return requestClient.get<{ url: string }>(
    `/api/core/file_manager/url/${fileId}`,
  );
}

/**
 * 批量获取文件访问URL
 * @param fileIds 文件ID数组
 */
async function getBatchFileUrls(fileIds: string[]) {
  const ids = fileIds.join(',');
  return requestClient.get<{ data: Record<string, string> }>(
    `/api/core/file_manager/batch/urls`,
    {
      params: { ids },
    },
  );
}

/**
 * 获取文件流式传输URL（用于img src等直接访问）
 * @param fileId 文件ID
 */
function getFileStreamUrl(fileId: string): string {
  // 获取访问令牌
  const accessStore = useAccessStore();
  const token = accessStore.accessToken;
  return `/basic-api/api/core/file_manager/stream/${fileId}?token=${token}`;
}

/**
 * 获取文件代理访问URL（用于img src等直接访问）
 * @param fileId 文件ID
 * @param download 是否下载
 */
function getFileProxyUrl(fileId: string, download = false): string {
  const params = download ? '?download=true' : '';
  return `/api/core/file_manager/proxy/${fileId}${params}`;
}

/**
 * 通过API客户端获取文件流（返回blob数据）
 * @param fileId 文件ID
 */
async function getFileStream(fileId: string) {
  return requestClient.get(`/api/core/file_manager/stream/${fileId}`, {
    responseType: 'blob',
  });
}

/**
 * 通过id获取文件信息
 * @param fileId 文件ID
 */
async function getFileInfo(fileId: string) {
  return requestClient.get(`/api/core/file_manager/file_info/${fileId}`);
}

/**
 * 通过多个文件ID批量获取文件信息
 * @param fileIds 文件ID数组
 * @returns 所有文件信息的数组
 */
async function getFilesInfo(fileIds: string[]) {
  if (!fileIds || fileIds.length === 0) {
    return [];
  }

  // 使用Promise.all并行请求所有文件信息
  const promises = fileIds.map((fileId) => getFileInfo(fileId));

  try {
    return await Promise.all(promises);
  } catch (error) {
    console.error('批量获取文件信息失败:', error);
    throw error; // 可以根据需要处理错误，这里选择抛出以便上层处理
  }
}

/**
 * 通过API客户端获取文件代理数据（返回blob数据）
 * @param fileId 文件ID
 * @param download 是否下载
 */
async function getFileProxy(fileId: string, download = false) {
  return requestClient.get(`/api/core/file_manager/proxy/${fileId}`, {
    params: download ? { download: true } : undefined,
    responseType: 'blob',
  });
}

export {
  batchDelete,
  createFolder,
  deleteItem,
  getBatchFileUrls,
  getDownloadUrl,
  getFileInfo,
  getFileList,
  getFileProxy,
  getFileProxyUrl,
  getFilesInfo,
  getFileStream,
  getFileStreamUrl,
  getFileUrlById,
  getFolderTree,
  getStorageConfig,
  moveItems,
  renameItem,
  updateStorageConfig,
  uploadFile,
};
