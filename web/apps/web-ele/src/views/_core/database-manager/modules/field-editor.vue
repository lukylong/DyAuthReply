<script setup lang="ts">
import { computed } from 'vue';

import { $t } from '@vben/locales';
import { Plus, Trash } from '@vben/icons';

import {
  ElButton,
  ElCheckbox,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElTable,
  ElTableColumn,
} from 'element-plus';

interface FieldDefinition {
  name: string;
  type: string;
  length?: number;
  precision?: number;
  scale?: number;
  nullable: boolean;
  default?: string;
  primaryKey: boolean;
  unique: boolean;
  comment?: string;
}

interface Props {
  fields: FieldDefinition[];
  disabled?: boolean;
  dbType?: string;
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  dbType: 'postgresql',
});
const emit = defineEmits<{
  'update:fields': [fields: FieldDefinition[]];
}>();

// 数据类型定义
interface DataTypeOption {
  label: string;
  value: string;
  hasLength?: boolean;
  hasPrecision?: boolean;
  desc: string;
}

// PostgreSQL 数据类型
const postgresqlTypes: DataTypeOption[] = [
  {
    label: 'VARCHAR',
    value: 'varchar',
    hasLength: true,
    desc: '可变长度字符串',
  },
  { label: 'CHAR', value: 'char', hasLength: true, desc: '固定长度字符串' },
  { label: 'TEXT', value: 'text', desc: '无限长度文本' },
  { label: 'INTEGER', value: 'integer', desc: '整数 (4字节)' },
  { label: 'BIGINT', value: 'bigint', desc: '大整数 (8字节)' },
  { label: 'SMALLINT', value: 'smallint', desc: '小整数 (2字节)' },
  { label: 'SERIAL', value: 'serial', desc: '自增整数 (4字节)' },
  { label: 'BIGSERIAL', value: 'bigserial', desc: '自增大整数 (8字节)' },
  { label: 'DECIMAL', value: 'decimal', hasPrecision: true, desc: '精确小数' },
  { label: 'NUMERIC', value: 'numeric', hasPrecision: true, desc: '精确数值' },
  { label: 'REAL', value: 'real', desc: '单精度浮点 (4字节)' },
  {
    label: 'DOUBLE PRECISION',
    value: 'double precision',
    desc: '双精度浮点 (8字节)',
  },
  { label: 'BOOLEAN', value: 'boolean', desc: '布尔值' },
  { label: 'DATE', value: 'date', desc: '日期' },
  { label: 'TIME', value: 'time', desc: '时间' },
  { label: 'TIMESTAMP', value: 'timestamp', desc: '时间戳' },
  { label: 'TIMESTAMPTZ', value: 'timestamptz', desc: '带时区时间戳' },
  { label: 'INTERVAL', value: 'interval', desc: '时间间隔' },
  { label: 'JSON', value: 'json', desc: 'JSON数据' },
  { label: 'JSONB', value: 'jsonb', desc: '二进制JSON (可索引)' },
  { label: 'UUID', value: 'uuid', desc: '通用唯一标识符' },
  { label: 'BYTEA', value: 'bytea', desc: '二进制数据' },
  { label: 'INET', value: 'inet', desc: 'IP地址' },
  { label: 'CIDR', value: 'cidr', desc: '网络地址' },
  { label: 'MACADDR', value: 'macaddr', desc: 'MAC地址' },
  { label: 'ARRAY', value: 'array', desc: '数组类型' },
];

// MySQL 数据类型
const mysqlTypes: DataTypeOption[] = [
  {
    label: 'VARCHAR',
    value: 'varchar',
    hasLength: true,
    desc: '可变长度字符串 (最大65535)',
  },
  {
    label: 'CHAR',
    value: 'char',
    hasLength: true,
    desc: '固定长度字符串 (最大255)',
  },
  { label: 'TEXT', value: 'text', desc: '长文本 (最大65535)' },
  { label: 'MEDIUMTEXT', value: 'mediumtext', desc: '中等文本 (最大16MB)' },
  { label: 'LONGTEXT', value: 'longtext', desc: '超长文本 (最大4GB)' },
  { label: 'TINYINT', value: 'tinyint', desc: '微整数 (-128~127)' },
  { label: 'SMALLINT', value: 'smallint', desc: '小整数 (2字节)' },
  { label: 'MEDIUMINT', value: 'mediumint', desc: '中整数 (3字节)' },
  { label: 'INT', value: 'int', desc: '整数 (4字节)' },
  { label: 'BIGINT', value: 'bigint', desc: '大整数 (8字节)' },
  { label: 'DECIMAL', value: 'decimal', hasPrecision: true, desc: '精确小数' },
  { label: 'FLOAT', value: 'float', desc: '单精度浮点' },
  { label: 'DOUBLE', value: 'double', desc: '双精度浮点' },
  { label: 'BIT', value: 'bit', hasLength: true, desc: '位字段' },
  { label: 'BOOLEAN', value: 'boolean', desc: '布尔值 (TINYINT(1))' },
  { label: 'DATE', value: 'date', desc: '日期' },
  { label: 'TIME', value: 'time', desc: '时间' },
  { label: 'DATETIME', value: 'datetime', desc: '日期时间' },
  { label: 'TIMESTAMP', value: 'timestamp', desc: '时间戳' },
  { label: 'YEAR', value: 'year', desc: '年份' },
  { label: 'JSON', value: 'json', desc: 'JSON数据' },
  { label: 'BLOB', value: 'blob', desc: '二进制大对象' },
  { label: 'ENUM', value: 'enum', desc: '枚举类型' },
  { label: 'SET', value: 'set', desc: '集合类型' },
];

// SQL Server 数据类型
const sqlserverTypes: DataTypeOption[] = [
  {
    label: 'VARCHAR',
    value: 'varchar',
    hasLength: true,
    desc: '可变长度字符串',
  },
  {
    label: 'NVARCHAR',
    value: 'nvarchar',
    hasLength: true,
    desc: 'Unicode可变字符串',
  },
  { label: 'CHAR', value: 'char', hasLength: true, desc: '固定长度字符串' },
  {
    label: 'NCHAR',
    value: 'nchar',
    hasLength: true,
    desc: 'Unicode固定字符串',
  },
  { label: 'TEXT', value: 'text', desc: '长文本 (已弃用)' },
  { label: 'NTEXT', value: 'ntext', desc: 'Unicode长文本 (已弃用)' },
  { label: 'VARCHAR(MAX)', value: 'varchar(max)', desc: '最大可变字符串' },
  {
    label: 'NVARCHAR(MAX)',
    value: 'nvarchar(max)',
    desc: 'Unicode最大可变字符串',
  },
  { label: 'TINYINT', value: 'tinyint', desc: '微整数 (0~255)' },
  { label: 'SMALLINT', value: 'smallint', desc: '小整数 (2字节)' },
  { label: 'INT', value: 'int', desc: '整数 (4字节)' },
  { label: 'BIGINT', value: 'bigint', desc: '大整数 (8字节)' },
  { label: 'DECIMAL', value: 'decimal', hasPrecision: true, desc: '精确小数' },
  { label: 'NUMERIC', value: 'numeric', hasPrecision: true, desc: '精确数值' },
  { label: 'FLOAT', value: 'float', desc: '浮点数' },
  { label: 'REAL', value: 'real', desc: '单精度浮点' },
  { label: 'MONEY', value: 'money', desc: '货币 (8字节)' },
  { label: 'SMALLMONEY', value: 'smallmoney', desc: '小货币 (4字节)' },
  { label: 'BIT', value: 'bit', desc: '位 (0/1)' },
  { label: 'DATE', value: 'date', desc: '日期' },
  { label: 'TIME', value: 'time', desc: '时间' },
  { label: 'DATETIME', value: 'datetime', desc: '日期时间' },
  { label: 'DATETIME2', value: 'datetime2', desc: '高精度日期时间' },
  { label: 'DATETIMEOFFSET', value: 'datetimeoffset', desc: '带时区日期时间' },
  { label: 'SMALLDATETIME', value: 'smalldatetime', desc: '小日期时间' },
  { label: 'UNIQUEIDENTIFIER', value: 'uniqueidentifier', desc: 'GUID/UUID' },
  { label: 'BINARY', value: 'binary', hasLength: true, desc: '固定长度二进制' },
  {
    label: 'VARBINARY',
    value: 'varbinary',
    hasLength: true,
    desc: '可变长度二进制',
  },
  { label: 'IMAGE', value: 'image', desc: '图像数据 (已弃用)' },
  { label: 'XML', value: 'xml', desc: 'XML数据' },
];

// 根据数据库类型获取数据类型列表
const dataTypes = computed(() => {
  const dbType = props.dbType?.toLowerCase() || 'postgresql';
  switch (dbType) {
    case 'mysql': {
      return mysqlTypes;
    }
    case 'sqlserver': {
      return sqlserverTypes;
    }
    default: {
      return postgresqlTypes;
    }
  }
});

const localFields = computed({
  get: () => props.fields,
  set: (value) => emit('update:fields', value),
});

// 触发字段更新（用于属性变化时通知父组件）
function triggerUpdate() {
  emit('update:fields', [...props.fields]);
}

// 添加字段
function addField() {
  const newField: FieldDefinition = {
    name: '',
    type: 'varchar',
    length: 255,
    nullable: true,
    primaryKey: false,
    unique: false,
    default: undefined,
    comment: '',
  };
  localFields.value = [...localFields.value, newField];
}

// 删除字段
function deleteField(index: number) {
  localFields.value = localFields.value.filter((_, i) => i !== index);
  ElMessage.success($t('database-manager.fieldDeleted'));
}

// 上移字段
function moveUp(index: number) {
  if (index === 0) return;
  const newFields = [...localFields.value];
  const temp = newFields[index - 1];
  newFields[index - 1] = newFields[index];
  newFields[index] = temp;
  localFields.value = newFields;
}

// 下移字段
function moveDown(index: number) {
  if (index === localFields.value.length - 1) return;
  const newFields = [...localFields.value];
  const temp = newFields[index];
  newFields[index] = newFields[index + 1];
  newFields[index + 1] = temp;
  localFields.value = newFields;
}

// 检查类型是否需要长度
function needsLength(type: string) {
  const typeInfo = dataTypes.value.find(
    (t: DataTypeOption) => t.value === type,
  );
  return typeInfo?.hasLength || false;
}

// 检查类型是否需要精度
function needsPrecision(type: string) {
  const typeInfo = dataTypes.value.find(
    (t: DataTypeOption) => t.value === type,
  );
  return typeInfo?.hasPrecision || false;
}
</script>

<template>
  <div class="field-editor">
    <div class="mb-3 flex items-center justify-between">
      <div class="text-sm font-semibold">{{ $t('database-manager.fieldList') }}</div>
      <ElButton
        type="primary"
        size="small"
        @click="addField"
        :disabled="disabled"
      >
        <Plus :size="14" />
        <span class="ml-1">{{ $t('database-manager.addField') }}</span>
      </ElButton>
    </div>

    <ElTable :data="localFields" border stripe height="400">
      <ElTableColumn :label="$t('database-manager.serialNumber')" width="60" align="center">
        <template #default="{ $index }">
          {{ $index + 1 }}
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.fieldName')" width="150">
        <template #default="{ row }">
          <ElInput
            v-model="row.name"
            :placeholder="$t('database-manager.fieldNamePlaceholder')"
            size="small"
            @change="triggerUpdate"
          />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.dataType')" width="150">
        <template #default="{ row }">
          <ElSelect
            v-model="row.type"
            :placeholder="$t('database-manager.type')"
            size="small"
            @change="triggerUpdate"
          >
            <ElOption
              v-for="type in dataTypes"
              :key="type.value"
              :label="`${type.label} - ${type.desc}`"
              :value="type.value"
            >
              <div class="flex items-center justify-between">
                <span class="font-medium">{{ type.label }}</span>
                <span class="ml-2 text-xs text-gray-500">{{ type.desc }}</span>
              </div>
            </ElOption>
          </ElSelect>
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.lengthPrecision')" width="100">
        <template #default="{ row }">
          <ElInputNumber
            v-if="needsLength(row.type)"
            v-model="row.length"
            :min="1"
            :max="65535"
            size="small"
            controls-position="right"
            @change="triggerUpdate"
          />
          <ElInputNumber
            v-else-if="needsPrecision(row.type)"
            v-model="row.precision"
            :min="1"
            :max="65"
            size="small"
            controls-position="right"
            :disabled="row.name === 'id'"
            @change="triggerUpdate"
          />
          <span v-else class="text-xs text-gray-400">-</span>
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.decimalPlaces')" width="80">
        <template #default="{ row }">
          <ElInputNumber
            v-if="needsPrecision(row.type)"
            v-model="row.scale"
            :min="0"
            :max="30"
            size="small"
            controls-position="right"
            @change="triggerUpdate"
          />
          <span v-else class="text-xs text-gray-400">-</span>
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.nullable')" width="60" align="center">
        <template #default="{ row }">
          <ElCheckbox v-model="row.nullable" @change="triggerUpdate" />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.defaultValue')" width="120">
        <template #default="{ row }">
          <ElInput
            v-model="row.default"
            :placeholder="$t('database-manager.defaultValuePlaceholder')"
            size="small"
            @change="triggerUpdate"
          />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.primaryKey')" width="60" align="center">
        <template #default="{ row }">
          <ElCheckbox v-model="row.primaryKey" @change="triggerUpdate" />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.unique')" width="60" align="center">
        <template #default="{ row }">
          <ElCheckbox v-model="row.unique" @change="triggerUpdate" />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.comment')" min-width="150">
        <template #default="{ row }">
          <ElInput
            v-model="row.comment"
            :placeholder="$t('database-manager.fieldCommentPlaceholder')"
            size="small"
            @change="triggerUpdate"
          />
        </template>
      </ElTableColumn>

      <ElTableColumn :label="$t('database-manager.operation')" width="140" fixed="right" align="center">
        <template #default="{ row, $index }">
          <div class="flex justify-center gap-1">
            <ElButton
              size="small"
              type="danger"
              :disabled="row.name === 'id'"
              @click="deleteField($index)"
            >
              <Trash :size="14" />
            </ElButton>
          </div>
        </template>
      </ElTableColumn>
    </ElTable>

    <div v-if="localFields.length === 0" class="py-8 text-center text-gray-400">
      {{ $t('database-manager.noFields') }}
    </div>
  </div>
</template>

<style scoped>
.field-editor :deep(.el-input__inner),
.field-editor :deep(.el-input-number__inner) {
  text-align: left;
}
</style>
