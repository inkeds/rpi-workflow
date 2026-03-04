# 标杆模块定义

> 标杆模块是项目中第一个完整实现的模块，作为后续所有模块的参考基准。

## 一、标杆模块信息

| 字段 | 值 |
|------|-----|
| 模块名称 | [填写] |
| 选择理由 | [填写：为什么选这个模块作为标杆] |
| 模块路径 | [填写：如 `src/modules/user/`] |
| 负责人 | [填写] |
| 完成日期 | [填写] |

### 选择标准

标杆模块应满足以下条件（至少 3 项）：

- [ ] 包含完整 CRUD 操作
- [ ] 包含表格 + 表单交互
- [ ] 包含跨模块联动
- [ ] 业务复杂度中等（不过简也不过复杂）
- [ ] 能覆盖项目中 80% 的交互模式

## 二、目录结构规范

```
[模块名]/
├── api/                  # API 接口定义
│   └── index.ts
├── components/           # 模块私有组件
│   ├── [模块名]Table.vue
│   ├── [模块名]Form.vue
│   └── [模块名]Detail.vue
├── composables/          # 模块私有 hooks
│   └── use[模块名].ts
├── types/                # 类型定义
│   └── index.ts
├── constants/            # 常量定义
│   └── index.ts
└── index.vue             # 模块入口页面
```

### 命名规范

| 类型 | 命名规则 | 示例 |
|------|---------|------|
| 目录 | kebab-case | `user-management/` |
| 组件 | PascalCase | `UserTable.vue` |
| composable | camelCase + use 前缀 | `useUser.ts` |
| API 文件 | camelCase | `userApi.ts` |
| 类型文件 | camelCase | `userTypes.ts` |

## 三、交互模式基准

### 表格 CRUD

| 操作 | 交互方式 | 必须包含 |
|------|---------|---------|
| 新增 | Modal / Drawer | 表单验证、提交 loading、成功提示 |
| 编辑 | Modal / Drawer（同新增复用） | 数据回填、字段差异处理 |
| 删除 | 二次确认弹窗 | 确认文案、批量删除支持 |
| 查看 | Detail 面板 / 新页面 | 只读展示、返回入口 |
| 搜索 | 表格上方搜索栏 | 重置按钮、回车触发 |

### 表单设计

| 规则 | 说明 |
|------|------|
| 布局 | 统一使用 [填写：如 24 栅格 / flex] |
| 标签位置 | [填写：如 left / top] |
| 必填标识 | 红色星号 `*` |
| 校验时机 | blur + submit |
| 错误提示 | 字段下方红色文字 |

### 按钮规范

| 位置 | 按钮顺序（左→右） |
|------|------------------|
| 表格操作栏 | 新增 → 批量删除 → 导出 |
| 表单底部 | 取消 → 确定 |
| 详情页 | 返回 → 编辑 |

## 四、状态管理基准

### 模块状态结构

```typescript
// [模块名]Store 示例
interface [模块名]State {
  list: [模块名]Item[]
  loading: boolean
  pagination: { page: number; pageSize: number; total: number }
  currentItem: [模块名]Item | null
  formVisible: boolean
  formMode: 'create' | 'edit'
}
```

### 状态命名规范

| 状态类型 | 命名规则 | 示例 |
|---------|---------|------|
| 列表数据 | `list` / `[名词]List` | `userList` |
| 加载状态 | `loading` / `[动作]Loading` | `submitLoading` |
| 弹窗控制 | `[名词]Visible` | `formVisible` |
| 当前选中 | `current[名词]` | `currentUser` |

## 五、API 调用基准

### 接口定义规范

```typescript
// api/index.ts 示例
export const [模块名]Api = {
  getList: (params: ListParams) => request.get('/api/[模块名]', { params }),
  getDetail: (id: string) => request.get(`/api/[模块名]/${id}`),
  create: (data: CreateDTO) => request.post('/api/[模块名]', data),
  update: (id: string, data: UpdateDTO) => request.put(`/api/[模块名]/${id}`, data),
  remove: (id: string) => request.delete(`/api/[模块名]/${id}`),
  batchRemove: (ids: string[]) => request.post('/api/[模块名]/batch-delete', { ids }),
}
```

### 错误处理规范

| 错误类型 | 处理方式 | 用户提示 |
|---------|---------|---------|
| 网络错误 | 全局拦截器 | Toast "网络异常，请重试" |
| 业务错误 | 接口返回 code | Toast 后端返回的 message |
| 权限错误 | 401/403 | 跳转登录 / 提示无权限 |
| 表单校验 | 前端拦截 | 字段下方红色提示 |

## 六、联动实现基准

### 本模块触发的联动

| 触发事件 | 目标模块 | 联动方式 | 数据传递 |
|---------|---------|---------|---------|
| [填写] | [填写] | 事件总线 / API | [填写] |

### 本模块监听的联动

| 来源模块 | 监听事件 | 处理逻辑 | 失败策略 |
|---------|---------|---------|---------|
| [填写] | [填写] | [填写] | [填写] |

## 七、验收检查清单

后续模块开发时，对照标杆模块逐项检查：

- [ ] 目录结构与标杆一致
- [ ] 命名规范与标杆一致
- [ ] CRUD 交互方式与标杆一致（Modal/Drawer，非同级表单块）
- [ ] 表单校验时机与标杆一致（blur + submit）
- [ ] 按钮布局与标杆一致
- [ ] 状态管理结构与标杆一致
- [ ] API 调用方式与标杆一致
- [ ] 错误处理方式与标杆一致
- [ ] 联动实现方式与标杆一致
- [ ] 删除操作有二次确认
- [ ] 提交操作有 loading 状态

---
