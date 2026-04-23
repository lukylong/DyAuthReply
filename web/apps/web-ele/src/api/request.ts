/**
 * 该文件可自行根据业务逻辑进行调整
 */
import type { RequestClientOptions } from '@vben/request';

import { useAppConfig } from '@vben/hooks';
import { preferences } from '@vben/preferences';
import {
  authenticateResponseInterceptor,
  defaultResponseInterceptor,
  errorMessageResponseInterceptor,
  RequestClient,
} from '@vben/request';
import { useAccessStore } from '@vben/stores';

import { ElMessage } from 'element-plus';

import { useAuthStore } from '#/store';

import { refreshTokenApi } from './core';

const { apiURL } = useAppConfig(import.meta.env, import.meta.env.PROD);

function createRequestClient(baseURL: string, options?: RequestClientOptions) {
  const client = new RequestClient({
    ...options,
    baseURL,
    paramsSerializer: (params) => {
      const searchParams = new URLSearchParams();
      for (const key in params) {
        const value = params[key];
        if (value === undefined || value === null) {
          continue;
        }
        if (Array.isArray(value)) {
          value.forEach((v) => searchParams.append(key, v));
        } else {
          searchParams.append(key, value);
        }
      }
      return searchParams.toString();
    },
  });

  /**
   * 重新认证逻辑
   */
  async function doReAuthenticate() {
    console.warn('Access token or refresh token is invalid or expired. ');
    const accessStore = useAccessStore();
    const authStore = useAuthStore();
    accessStore.setAccessToken(null);
    if (
      preferences.app.loginExpiredMode === 'modal' &&
      accessStore.isAccessChecked
    ) {
      accessStore.setLoginExpired(true);
    } else {
      await authStore.logout();
    }
  }

  /**
   * 刷新token逻辑
   */
  async function doRefreshToken() {
    const accessStore = useAccessStore();

    // 检查 refreshToken 是否存在
    if (!accessStore.refreshToken) {
      console.error('Refresh token is missing');
      await doReAuthenticate();
      throw new Error('Refresh token is missing');
    }

    // 传递 refreshToken 给 API
    const resp = await refreshTokenApi(accessStore.refreshToken);

    // 处理响应中的新 token
    // 后端支持两种格式：直接返回 token 字符串 或 { token, accessToken } 对象
    const newToken = resp.data?.accessToken || '';

    // 更新 access token
    accessStore.setAccessToken(newToken);

    // 如果响应中有新的 refresh token，也保存
    if (typeof resp.data === 'object' && resp.data?.refreshToken) {
      accessStore.setRefreshToken(resp.data.refreshToken);
    }

    return newToken;
  }

  function formatToken(token: null | string) {
    return token ? `Bearer ${token}` : null;
  }

  // 请求头处理
  client.addRequestInterceptor({
    fulfilled: async (config) => {
      const accessStore = useAccessStore();

      config.headers.Authorization = formatToken(accessStore.accessToken);
      config.headers['Accept-Language'] = preferences.app.locale;
      return config;
    },
  });

  // 处理返回的响应数据格式
  client.addResponseInterceptor(
    defaultResponseInterceptor({
      codeField: 'code',
      dataField: 'data',
      successCode: 0,
    }),
  );

  // token过期的处理
  client.addResponseInterceptor(
    authenticateResponseInterceptor({
      client,
      doReAuthenticate,
      doRefreshToken,
      enableRefreshToken: preferences.app.enableRefreshToken,
      formatToken,
      // 登录接口的401错误不应该触发重新认证，应该显示错误消息
      shouldHandle: (error) => {
        const url = error?.config?.url || '';
        // 登录、刷新token等接口的401错误不处理，让错误消息拦截器处理
        return !url.includes('/login') && !url.includes('/refresh_token');
      },
    }),
  );

  // 通用的错误处理,如果没有进入上面的错误处理逻辑，就会进入这里
  client.addResponseInterceptor(
    errorMessageResponseInterceptor((msg: string, error) => {
      // 这里可以根据业务进行定制,你可以拿到 error 内的信息进行定制化处理，根据不同的 code 做不同的提示，而不是直接使用 message.error 提示 msg
      const responseData = error?.response?.data ?? {};
      const status = error?.response?.status;

      // 优先级顺序：后端自定义消息 > 传入的消息 > 状态码默认消息
      let errorMessage =
        responseData?.message ||
        responseData?.error ||
        responseData?.detail ||
        responseData?.msg ||
        msg;

      // 特殊处理429限流
      if (status === 429) {
        errorMessage =
          errorMessage || '请求过于频繁，请稍后再试（5分钟内不能重试）';
      }

      // 特殊处理403禁止访问
      if (status === 403) {
        errorMessage = errorMessage || '您没有权限访问此资源';
      }

      // 特殊处理401未授权
      if (status === 401) {
        errorMessage = errorMessage || '认证失败，请重新登录';
      }

      // 打印完整错误信息便于调试
      console.error('[API Error]', {
        status,
        message: errorMessage,
        data: responseData,
      });
      // 显示错误消息
      if (errorMessage) {
        ElMessage.error(errorMessage || msg);
      }
    }),
  );

  return client;
}

export const requestClient = createRequestClient(apiURL, {
  responseReturn: 'body',
});

export const baseRequestClient = new RequestClient({ baseURL: apiURL });
