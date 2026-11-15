# 前端布局与响应式约定

本文说明 `frontend/` 的布局结构、断点约定，以及两条**很容易踩且看不出来**的坑。
改前端前先读一遍，能省掉大量「明明写了 `hidden` 却还在显示」的排查时间。

---

## 一、两种布局形态

`StudioPage.vue` 会根据视口宽度渲染**两套互斥的 DOM**（`v-if` / `v-else`，不是 CSS 隐藏）：

| 形态 | 条件 | 结构 |
|---|---|---|
| 宽屏 | `>= 992px` | TDesign 栅格三栏并排：创作 / 作品库 / 详情，各自内部独立滚动 |
| 窄屏 | `< 992px` | 单栏 + 顶部分段切换（`t-radio-group`）：创作 / 作品库 / 详情，一次只显示一个分区 |

### 为什么必须互斥（不能用 CSS 隐藏）

作品库卡片带 `id="song-<作品id>"`，`showLibrary()` 用它做 `scrollIntoView` 定位。
两套 DOM 同时存在就会产生**重复 id**，`document.getElementById` 只会命中第一个
（很可能在隐藏的那套里），定位会静默失效。

### 窄屏下的交互约定

- 点作品卡片 = 打开详情，自动切到「详情」分区（`selectSong()` 里做的）；
- 详情面板的关闭按钮 = 退回「作品库」分区（`handleCloseDetail()`）；
- 播放条上的「作品库」按钮 = 切到「作品库」分区并滚动定位（`showLibrary()`，
  切换后要 `await nextTick()` 等 DOM 挂载，否则滚不动）。

---

## 二、断点：Tailwind 与 TDesign 栅格对齐

`tailwind.config.js` 里的 `theme.screens` **整体替换**（不是 `extend`）了 Tailwind 默认断点，
改成与 TDesign 栅格（`tdesign-vue-next` 的 `t-row` / `t-col`）完全一致：

| 名称 | 宽度 | 用途 |
|---|---|---|
| `xs` | < 768px | 手机竖屏 |
| `sm` | >= 768px | 手机横屏 / 小平板 |
| `md` | >= 992px | **桌面**：三栏栅格与单栏切换的分界 |
| `lg` | >= 1200px | 宽屏桌面：列宽 `3/6/3` |
| `xl` | >= 1400px | 大屏 |
| `xxl` | >= 1880px | 超宽屏 |

**所以 `md:` 前缀表示 992px，不是 Tailwind 默认的 768px。**
两套体系混用会让「CSS 媒体查询」和「组件栅格」在同一宽度上给出两套答案。

宽屏下的列宽：

```html
<t-col :xs="12" :md="4" :lg="3">简单创作</t-col>   <!-- md: 4/12，lg: 3/12 -->
<t-col :xs="12" :md="5" :lg="6">作品库</t-col>     <!-- md: 5/12，lg: 6/12 -->
<t-col :xs="12" :md="3" :lg="3">歌曲详情</t-col>   <!-- md: 3/12，lg: 3/12 -->
```

`md` 断点下详情列只有约 250px，所以详情面板内部（歌词校准按钮组、封面尺寸）
都做了窄栏适配——按钮组允许换行，封面从 88px 缩到 72px。

---

## 三、坑一：TDesign 组件样式压过 Tailwind 工具类

**产物 CSS 里 TDesign 的组件样式排在 Tailwind 工具类之后**（实测 `dist` 产物中
`.t-button` 的位置 39065 > `.hidden` 的位置 18998），而两者选择器优先级相同
（都是单类 `0,1,0`），于是**后者胜：TDesign 赢**。

后果：直接给 TDesign 组件加响应式隐藏是**无效**的：

```html
<!-- ❌ 无效：.t-button { display: flex } 盖掉了 .hidden { display: none } -->
<TButton class="hidden md:inline-flex" />

<!-- ✅ 有效：用普通元素承载响应式类 -->
<span class="hidden md:inline-flex"><TButton /></span>
```

普通元素（`span` / `div` / `p`）上没有 TDesign 的 display 规则，工具类才能正常生效。
`.t-icon` 同理（`display: inline-block`）。

> 同源问题：自定义类与 TDesign 同名选择器冲突时，也要提高优先级。
> 例：`.t-radio-group` 自带 `display:inline-flex; width:fit-content`，
> 把它改成撑满一行必须写 `.ace-segment.t-radio-group`（`0,2,0`），
> 只写 `.ace-segment`（`0,1,0`）会因注入顺序而失效。

**排查手法**：控制台里查元素的 `getComputedStyle(el).display`，
以及 `document.styleSheets` 的顺序，比肉眼看截图快得多。

---

## 四、坑二：触屏没有 hover

窄屏下这些交互必须常驻显示，不能依赖 `hover`：

- **作品库卡片封面的播放按钮**：`opacity-100 md:opacity-0 md:group-hover:opacity-100`。
  纯 `hover` 的写法在手机上按钮永远不可见，等于手机没法从列表起播。
- **卡片更多菜单**（下载音频）靠点击打开，本身不依赖 hover，没问题。

---

## 五、组件职责

| 文件 | 职责 |
|---|---|
| `pages/StudioPage.vue` | 全局状态中枢 + 布局形态切换；持有唯一的 `<audio>` |
| `components/CreatePanel.vue` | 左栏创作表单（描述、纯音乐开关、提交） |
| `components/LibraryPanel.vue` | 中栏作品库卡片列表（生成中 / 已完成 / 失败） |
| `components/SongDetailPanel.vue` | 右栏详情（封面、同步歌词、元信息、下载） |
| `components/LyricScroller.vue` | 同步滚动歌词（`transform` 定位当前行） |
| `components/PlaybackBar.vue` | 底部播放条（宽屏经典三段式 / 窄屏紧凑单行） |
| `components/MiniPlayer.vue` | 播放条收起后的悬浮入口 |
| `common/useMediaQuery.ts` | 把媒体查询结果暴露成 ref，供布局形态切换 |
| `common/lyric.ts` | 歌词时间轴（优先 LRC，否则按字重估算） |
| `common/utils.ts` | 格式化、配色、图标等纯函数 |

子组件全部是**受控组件**：只收 props、把用户操作 `emit` 回 `StudioPage`，
自己不发请求、不碰 `<audio>`。

---

## 六、本地验证

```bash
cd frontend
npm run dev          # http://localhost:5175
npm run type-check   # vue-tsc
npm run build        # 类型检查 + 产物构建
```

验证响应式时，至少看这三个宽度：360px（窄屏）、768px（平板，仍是单栏）、
1440px（桌面三栏）。判断标准：

- `document.documentElement.scrollWidth === innerWidth`（无横向溢出）；
- 窄屏下分段切换可点、点卡片能进详情、播放器按钮都点得到；
- 宽屏下三列等高铺满、各自内部滚动，播放条是经典三段式。
