/**
 * 客户端公告 API
 */
import { requestClient } from '#/api/request';

export namespace ClientAnnouncementApi {
  /** 公告列表查询参数 */
  export interface QueryParams {
    page?: number;
    page_size?: number;
    status?: 'draft' | 'published' | 'revoked';
    level?: 'info' | 'warning' | 'urgent';
  }

  /** 公告列表响应 */
  export interface ListResponse {
    items: AnnouncementItem[];
    total: number;
  }

  /** 公告项 */
  export interface AnnouncementItem {
    id: string;
    title: string;
    content: string;
    level: 'info' | 'warning' | 'urgent';
    status: 'draft' | 'published' | 'revoked';
    publish_time: string | null;
    expire_time: string | null;
    target_version: string | null;
    sys_create_datetime: string;
    sys_update_datetime: string;
  }

  /** 创建公告 DTO */
  export interface CreateDto {
    title: string;
    content: string;
    level?: 'info' | 'warning' | 'urgent';
    status?: 'draft' | 'published' | 'revoked';
    publish_time?: string | null;
    expire_time?: string | null;
    target_version?: string | null;
  }

  /** 更新公告 DTO */
  export interface UpdateDto {
    title?: string;
    content?: string;
    level?: 'info' | 'warning' | 'urgent';
    status?: 'draft' | 'published' | 'revoked';
    publish_time?: string | null;
    expire_time?: string | null;
    target_version?: string | null;
  }

  /** 获取公告列表 */
  export function getList(params?: QueryParams) {
    return requestClient.get<ListResponse>('/core/client-announcement', { params });
  }

  /** 创建公告 */
  export function create(data: CreateDto) {
    return requestClient.post<AnnouncementItem>('/core/client-announcement', data);
  }

  /** 更新公告 */
  export function update(id: string, data: UpdateDto) {
    return requestClient.put<AnnouncementItem>(`/core/client-announcement/${id}`, data);
  }

  /** 删除公告 */
  export function remove(id: string) {
    return requestClient.delete(`/core/client-announcement/${id}`);
  }

  /** 发布公告 */
  export function publish(id: string) {
    return requestClient.post<AnnouncementItem>(`/core/client-announcement/${id}/publish`);
  }
}
