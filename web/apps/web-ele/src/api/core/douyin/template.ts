import { requestClient } from '#/api/request';

/** 模板分类 */
export interface DouyinTemplateCategory {
  id: string;
  name: string;
  icon: string;
  color: string;
  parent_id?: null | string;
  template_count?: number;
  children?: DouyinTemplateCategory[];
  remark?: null | string;
}

export interface DouyinTemplateCategoryInput {
  name: string;
  icon?: string;
  color?: string;
  parent_id?: null | string;
  remark?: null | string;
}

/** 回复模板 */
export interface DouyinTemplateLink {
  title: string;
  url: string;
  note?: string;
}

export interface DouyinTemplate {
  id: string;
  name: string;
  category_id?: null | string;
  category_name?: null | string;
  content: string;
  links: DouyinTemplateLink[];
  variables: string[];
  send_mode: 'card_fallback' | 'merged' | 'multi_message';
  status: boolean;
  is_shared: boolean;
  owner_id?: null | string;
  owner_name?: null | string;
  use_count: number;
  remark?: null | string;
  sort?: number;
  sys_create_datetime?: string;
}

export interface DouyinTemplateInput {
  name: string;
  category_id?: null | string;
  content: string;
  links?: DouyinTemplateLink[];
  send_mode?: 'card_fallback' | 'merged' | 'multi_message';
  status?: boolean;
  is_shared?: boolean;
  remark?: null | string;
}

export interface DouyinTemplateListParams {
  page?: number;
  page_size?: number;
  name?: string;
  category_id?: string;
  status?: boolean;
  is_shared?: boolean;
}

export interface PaginatedResponse<T> {
  total: number;
  items: T[];
}

// ---------- 模板分类 ----------
export async function getCategoryTree() {
  return requestClient.get<DouyinTemplateCategory[]>('/api/core/douyin/template-category/tree');
}

export async function getCategoryList() {
  return requestClient.get<PaginatedResponse<DouyinTemplateCategory>>(
    '/api/core/douyin/template-category',
    { params: { page_size: 100 } },
  );
}

export async function createCategory(data: DouyinTemplateCategoryInput) {
  return requestClient.post<DouyinTemplateCategory>('/api/core/douyin/template-category', data);
}

export async function updateCategory(id: string, data: DouyinTemplateCategoryInput) {
  return requestClient.put<DouyinTemplateCategory>(`/api/core/douyin/template-category/${id}`, data);
}

export async function deleteCategory(id: string) {
  return requestClient.delete(`/api/core/douyin/template-category/${id}`);
}

// ---------- 模板 ----------
export async function getTemplateList(params: DouyinTemplateListParams = {}) {
  return requestClient.get<PaginatedResponse<DouyinTemplate>>('/api/core/douyin/template', { params });
}

export async function getAllTemplate() {
  return requestClient.get<DouyinTemplate[]>('/api/core/douyin/template/all');
}

export async function getTemplate(id: string) {
  return requestClient.get<DouyinTemplate>(`/api/core/douyin/template/${id}`);
}

export async function createTemplate(data: DouyinTemplateInput) {
  return requestClient.post<DouyinTemplate>('/api/core/douyin/template', data);
}

export async function updateTemplate(id: string, data: DouyinTemplateInput) {
  return requestClient.put<DouyinTemplate>(`/api/core/douyin/template/${id}`, data);
}

export async function patchTemplate(id: string, data: Partial<DouyinTemplateInput>) {
  return requestClient.patch<DouyinTemplate>(`/api/core/douyin/template/${id}`, data);
}

export async function deleteTemplate(id: string) {
  return requestClient.delete(`/api/core/douyin/template/${id}`);
}

export async function batchDeleteTemplate(ids: string[]) {
  return requestClient.post('/api/core/douyin/template/batch/delete', { ids });
}

export async function previewTemplate(template_id: string, context: Record<string, string> = {}) {
  return requestClient.post<{ rendered: string; links: DouyinTemplateLink[] }>(
    '/api/core/douyin/template/preview',
    { template_id, context },
  );
}
