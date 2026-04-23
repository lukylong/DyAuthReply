<script lang="ts" setup>
import type { User } from '#/api/core/user';

import { computed, onMounted, ref, watch } from 'vue';

import { ElAvatar, ElPopover, ElSkeleton } from 'element-plus';

import { getFileStreamUrl } from '#/api/core/file';
import { getUserDetailApi } from '#/api/core/user';
import { generateAvatarGradient, generateAvatarText } from '#/utils/avatar';

defineOptions({
  name: 'UserAvatar',
});

const props = withDefaults(defineProps<Props>(), {
  size: 56,
  fontSize: 24,
  shadow: true,
  showPopover: true,
  autoLoad: false,
  showInfo: false,
});

interface Props {
  /**
   * 用户对象（包含 id, name, avatar 等信息）
   */
  user?: User;
  /**
   * 用户ID（用于获取详细信息和用户对象）
   */
  userId?: string;
  /**
   * 用户名字或用户名（可选，如果不提供则从 user 或 userId 中获取）
   */
  name?: string;
  /**
   * 头像文件路径（不需要调用 getFileStreamUrl，组件内部处理）
   * @deprecated 建议使用 user 或 userId 代替
   */
  avatar?: string;
  /**
   * 头像尺寸（像素）
   * @default 56
   */
  size?: number;
  /**
   * 文字大小（像素）
   * @default 24
   */
  fontSize?: number;
  /**
   * 是否显示阴影
   * @default true
   */
  shadow?: boolean;
  /**
   * 是否启用悬停显示详细信息
   * @default true
   */
  showPopover?: boolean;
  /**
   * 是否自动加载用户信息（用于只传 userId 时自动获取用户名和头像）
   * @default false
   */
  autoLoad?: boolean;
  /**
   * 是否在头像下方显示用户名和username
   * @default false
   */
  showInfo?: boolean;
}

// 获取有效的 ID
const effectiveUserId = computed(() => {
  return props.userId || props.user?.id;
});

// 获取有效的用户名
const effectiveUserName = computed(() => {
  return props.name || props.user?.name || userDetail.value?.name;
});

// 获取有效的头像 URL（处理 getFileStreamUrl）
const effectiveAvatarUrl = computed(() => {
  const avatarPath =
    props.avatar || props.user?.avatar || userDetail.value?.avatar;
  if (avatarPath) {
    return getFileStreamUrl(avatarPath);
  }
  return undefined;
});

const userInitials = computed(() => {
  return generateAvatarText(effectiveUserName.value);
});

const avatarGradient = computed(() => {
  return generateAvatarGradient(effectiveUserName.value);
});

// Popover 相关
const popoverVisible = ref(false);
const userDetail = ref<User>();
const loading = ref(false);
const hasLoaded = ref(false);

// 加载用户详细信息
async function loadUserDetail() {
  if (hasLoaded.value) return; // 已加载过，不再重复加载

  // 如果已有用户对象，直接使用；否则通过 ID 加载
  if (props.user) {
    userDetail.value = props.user;
    hasLoaded.value = true;
    return;
  }

  if (!effectiveUserId.value) return;

  loading.value = true;
  try {
    userDetail.value = await getUserDetailApi(effectiveUserId.value);
    hasLoaded.value = true;
  } catch (error) {
    console.error('Failed to load user detail:', error);
  } finally {
    loading.value = false;
  }
}

// Popover 显示时加载数据
function handlePopoverShow() {
  if (!hasLoaded.value) {
    loadUserDetail();
  }
}

// 获取性别显示文本
function getGenderText(gender?: number) {
  if (gender === 1) return '男';
  if (gender === 0) return '女';
  return '未知';
}

// 获取状态标签类型
function getStatusType(isActive?: number) {
  return isActive === 1 ? 'success' : 'danger';
}

// 获取状态显示文本
function getStatusText(isActive?: number) {
  return isActive === 1 ? '启用' : '禁用';
}

// 自动加载用户信息
onMounted(() => {
  // autoLoad 或 showInfo 时都需要加载用户详情
  if ((props.autoLoad || props.showInfo) && props.userId && !props.user) {
    loadUserDetail();
  }
});

// 监听 userId 变化，自动重新加载
watch(
  () => props.userId,
  (newId, oldId) => {
    if ((props.autoLoad || props.showInfo) && newId && newId !== oldId) {
      hasLoaded.value = false;
      userDetail.value = undefined;
      loadUserDetail();
    }
  },
);
</script>

<template>
  <ElPopover
    v-if="showPopover && effectiveUserId"
    v-model:visible="popoverVisible"
    placement="right"
    :width="280"
    trigger="hover"
    :show-after="300"
    @show="handlePopoverShow"
  >
    <template #reference>
      <div class="avatar-wrapper" :class="{ 'with-info': showInfo }">
        <div
          class="avatar-generator cursor-pointer"
          :class="{ 'is-loading': loading }"
        >
          <div
            v-if="!effectiveAvatarUrl"
            class="avatar-gradient"
            :style="{
              width: `${size}px`,
              height: `${size}px`,
              background: avatarGradient,
              boxShadow: shadow ? '0 2px 8px rgba(0, 0, 0, 0.15)' : 'none',
              fontSize: `${fontSize}px`,
            }"
          >
            <span class="avatar-text">{{ userInitials }}</span>
          </div>
          <ElAvatar
            v-else
            :src="effectiveAvatarUrl"
            :alt="effectiveUserName"
            :size="size"
            :style="{
              boxShadow: shadow ? '0 2px 8px rgba(0, 0, 0, 0.15)' : 'none',
            }"
          />
        </div>
        <div v-if="showInfo" class="avatar-info">
          <div v-if="loading" class="info-skeleton">
            <div class="skeleton-line name"></div>
            <div class="skeleton-line username"></div>
          </div>
          <template v-else-if="userDetail">
            <div class="info-name">{{ userDetail.name }}</div>
            <div class="info-username">@{{ userDetail.username }}</div>
          </template>
        </div>
      </div>
    </template>

    <!-- Popover 内容 -->
    <div class="user-detail-popover">
      <!-- 加载骨架屏 -->
      <ElSkeleton v-if="loading || !userDetail" :rows="5" animated />

      <!-- 用户详细信息 -->
      <div v-if="userDetail && !loading" class="user-detail-content">
        <!-- 头像和基本信息 -->
        <div class="user-header">
          <div class="user-avatar-large">
            <div
              v-if="!userDetail.avatar"
              class="avatar-gradient"
              :style="{
                width: '64px',
                height: '64px',
                background: generateAvatarGradient(userDetail.name),
                fontSize: '24px',
              }"
            >
              <span class="avatar-text">{{
                generateAvatarText(userDetail.name)
              }}</span>
            </div>
            <ElAvatar
              v-else
              :src="getFileStreamUrl(userDetail.avatar || '')"
              :alt="userDetail.name"
              :size="64"
            />
          </div>
          <div class="user-basic-info">
            <div class="user-name">{{ userDetail.name }}</div>
            <div class="user-username">@{{ userDetail.username }}</div>
          </div>
        </div>

        <!-- 详细信息 -->
        <div class="user-details">
          <div v-if="userDetail.email" class="detail-item">
            <span class="detail-label">邮箱：</span>
            <span class="detail-value">{{ userDetail.email }}</span>
          </div>
          <div v-if="userDetail.mobile" class="detail-item">
            <span class="detail-label">手机：</span>
            <span class="detail-value">{{ userDetail.mobile }}</span>
          </div>
          <div v-if="userDetail.city" class="detail-item">
            <span class="detail-label">城市：</span>
            <span class="detail-value">{{ userDetail.city }}</span>
          </div>
          <div v-if="userDetail.dept_name" class="detail-item">
            <span class="detail-label">部门：</span>
            <span class="detail-value">{{ userDetail.dept_name }}</span>
          </div>
          <div
            v-if="userDetail.post_names && userDetail.post_names.length > 0"
            class="detail-item"
          >
            <span class="detail-label">岗位：</span>
            <span class="detail-value">{{
              userDetail.post_names.join(', ')
            }}</span>
          </div>
          <div v-if="userDetail.manager_name" class="detail-item">
            <span class="detail-label">主管：</span>
            <span class="detail-value">{{ userDetail.manager_name }}</span>
          </div>
          <!-- <div class="detail-item">
            <span class="detail-label">性别：</span>
            <span class="detail-value">{{ getGenderText(userDetail.gender) }}</span>
          </div> -->
          <div v-if="userDetail.user_type_display" class="detail-item">
            <span class="detail-label">类型：</span>
            <span class="detail-value">{{ userDetail.user_type_display }}</span>
          </div>
          <!-- <div class="detail-item">
            <span class="detail-label">状态：</span>
            <ElTag :type="getStatusType(userDetail.is_active)" size="small">
              {{ getStatusText(userDetail.is_active) }}
            </ElTag>
          </div> -->
        </div>
      </div>
    </div>
  </ElPopover>

  <!-- 无 Popover 时 -->
  <div v-else class="avatar-wrapper" :class="{ 'with-info': showInfo }">
    <div class="avatar-generator" :class="{ 'is-loading': loading }">
      <div
        v-if="!effectiveAvatarUrl"
        class="avatar-gradient"
        :style="{
          width: `${size}px`,
          height: `${size}px`,
          background: avatarGradient,
          boxShadow: shadow ? '0 2px 8px rgba(0, 0, 0, 0.15)' : 'none',
          fontSize: `${fontSize}px`,
        }"
      >
        <span class="avatar-text">{{ userInitials }}</span>
      </div>
      <ElAvatar
        v-else
        :src="effectiveAvatarUrl"
        :alt="effectiveUserName"
        :size="size"
        :style="{
          boxShadow: shadow ? '0 2px 8px rgba(0, 0, 0, 0.15)' : 'none',
        }"
      />
    </div>
    <div v-if="showInfo" class="avatar-info">
      <div v-if="loading" class="info-skeleton">
        <div class="skeleton-line name"></div>
        <div class="skeleton-line username"></div>
      </div>
      <template v-else-if="userDetail">
        <div class="info-name">{{ userDetail.name }}</div>
        <div class="info-username">@{{ userDetail.username }}</div>
      </template>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.avatar-wrapper {
  display: inline-flex;
  align-items: center;

  &.with-info {
    flex-direction: column;
    gap: 6px;
  }
}

.avatar-generator {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;

  &.is-loading {
    .avatar-gradient,
    :deep(.el-avatar) {
      animation: pulse 1.5s ease-in-out infinite;
    }
  }

  .avatar-gradient {
    display: flex;
    flex-shrink: 0;
    align-items: center;
    justify-content: center;
    border-radius: 50%;

    .avatar-text {
      font-weight: 700;
      color: white;
      white-space: nowrap;
      text-shadow: 0 1px 2px rgb(0 0 0 / 20%);
    }
  }

  :deep(.el-avatar) {
    flex-shrink: 0;
  }
}

.avatar-info {
  min-width: 60px;
  max-width: 80px;
  text-align: center;

  .info-name {
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 12px;
    font-weight: 500;
    color: var(--el-text-color-primary);
    white-space: nowrap;
  }

  .info-username {
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 11px;
    color: var(--el-text-color-secondary);
    white-space: nowrap;
  }

  .info-skeleton {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: center;

    .skeleton-line {
      background: linear-gradient(
        90deg,
        var(--el-fill-color) 25%,
        var(--el-fill-color-light) 50%,
        var(--el-fill-color) 75%
      );
      background-size: 200% 100%;
      border-radius: 4px;
      animation: shimmer 1.5s infinite;

      &.name {
        width: 48px;
        height: 12px;
      }

      &.username {
        width: 56px;
        height: 10px;
      }
    }
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.5;
  }
}

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }

  100% {
    background-position: -200% 0;
  }
}

.user-detail-popover {
  padding: 4px;

  .user-detail-content {
    .user-header {
      display: flex;
      gap: 12px;
      align-items: center;
      padding-bottom: 16px;
      border-bottom: 1px solid hsl(var(--border));

      .user-avatar-large {
        flex-shrink: 0;

        .avatar-gradient {
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 50%;
          box-shadow: 0 2px 8px rgb(0 0 0 / 15%);

          .avatar-text {
            font-weight: 700;
            color: white;
            text-shadow: 0 1px 2px rgb(0 0 0 / 20%);
          }
        }
      }

      .user-basic-info {
        flex: 1;
        min-width: 0;

        .user-name {
          margin-bottom: 4px;
          overflow: hidden;
          text-overflow: ellipsis;
          font-size: 16px;
          font-weight: 600;
          color: hsl(var(--foreground));
          white-space: nowrap;
        }

        .user-username {
          overflow: hidden;
          text-overflow: ellipsis;
          font-size: 13px;
          color: hsl(var(--muted-foreground));
          white-space: nowrap;
        }
      }
    }

    .user-details {
      padding-top: 12px;

      .detail-item {
        display: flex;
        align-items: center;
        padding: 6px 0;
        font-size: 13px;

        .detail-label {
          flex-shrink: 0;
          width: 60px;
          color: hsl(var(--muted-foreground));
        }

        .detail-value {
          flex: 1;
          overflow: hidden;
          text-overflow: ellipsis;
          color: hsl(var(--foreground));
          white-space: nowrap;
        }
      }
    }
  }
}
</style>
