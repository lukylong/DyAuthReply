export interface MenuItem {
  id: string;
  title?: string;
  name?: string;
  icon?: string;
  path?: string;
  children?: MenuItem[];
}

export interface MenuSelectorProps {
  modelValue?: null | string | string[];
  mode?: 'dialog' | 'popup';
  placeholder?: string;
  disabled?: boolean;
  clearable?: boolean;
  multiple?: boolean;
  dialogTitle?: string;
  dialogWidth?: string;
}

export interface MenuSelectorEmits {
  (e: 'update:modelValue', value: null | string | string[]): void;
  (e: 'change', menu: MenuItem | MenuItem[] | null): void;
}
