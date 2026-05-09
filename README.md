# HotVideo - 短视频爆款分析平台

## 🎯 项目简介
**免费一键提取抖音、小红书视频文案**，基于 AI 视觉理解和内容分析的短视频爆款研究平台，自动分析抖音/小红书爆款视频的成功机制，并生成可直接使用的 AI 视频创作提示词。

## ✨ 核心功能

### 📝 免费一键提取文案（核心功能）
- **抖音视频文案提取**：粘贴链接即可提取视频文案，无需登录，完全免费
- **小红书笔记文案提取**：支持小红书图文/视频笔记内容提取
- **视频语音转文字**：使用 AI 语音识别，准确率高达 95%+
- **批量提取**：支持批量处理多个视频链接
- **导出格式**：支持 TXT、Markdown、JSON 等多种格式导出

### 🔍 爆款分析
- **视频截帧**：随机截取视频关键帧
- **视觉分析**：使用 GLM-4.1V 深度理解画面内容
- **文案分析**：结合标题、文案、互动数据综合分析
- **爆点洞察**：挖掘视频爆火的真实原因（非表面描述）

### 🎬 AI 视频创作
- **创作提示词**：生成可直接用于 Sora/可灵/即梦 的详细视频描述
- **视觉细节**：包含人物设定、场景设定、镜头语言、动作设计
- **音乐氛围**：推荐音乐风格和情绪基调

### 📊 数据管理
- **多平台支持**：抖音、小红书、视频号
- **爆款阈值**：智能过滤低互动视频（`点赞×0.5 + 评论×0.3 + 分享×0.2 + 收藏×0.1 ≥ 5000`）
- **数据库存储**：保存分析结果，支持重复利用

## 🏗️ 技术架构

### 后端 (Python FastAPI)
- **框架**：FastAPI + Uvicorn
- **数据库**：PostgreSQL + SQLAlchemy
- **AI 模型**：
  - 视觉分析：智谱 GLM-4.1V-Thinking-Flash
  - 文本分析：智谱 glm-4-flash
  - ASR：SiliconFlow FunAudioLLM/SenseVoiceSmall
- **视频处理**：FFmpeg 截帧
- **浏览器自动化**：Playwright + mitmproxy

### 前端 (Vue.js)
- **框架**：Vue 3 + Vite
- **UI 组件**：Element Plus
- **数据可视化**：ECharts
- **视频播放**：Video.js

### 部署
- **容器化**：Docker + Docker Compose
- **服务**：
  - hotvideo-api：主 API 服务
  - hotvideo-db：PostgreSQL 数据库
  - hotvideo-redis：Redis 缓存
  - hotvideo-browser：浏览器池

## 🚀 快速开始

### 环境要求
- Docker & Docker Compose
- 智谱 API Key（用于 GLM-4.1V 和 glm-4-flash）
- SiliconFlow API Key（用于语音转文字）

### 部署步骤
```bash
# 1. 克隆项目
git clone https://github.com/lostchris123/hotvideo.git
cd hotvideo

# 2. 配置环境变量
cp api/.env.example api/.env
# 编辑 api/.env，填入 API Key

# 3. 启动服务
docker-compose up -d

# 4. 访问前端
# 浏览器打开 http://localhost:3000
```

### API 接口
```bash
# 1. 获取视频列表
GET /api/videos?platform=douyin&limit=100

# 2. 视觉爆款分析
POST /api/videos/{video_id}/visual-analyze
# 参数：platform=douyin, force=false, num_frames=3
```

## 📁 项目结构
```
hotvideo/
├── api/                    # 后端 API
│   ├── main.py            # 主应用入口
│   ├── analyzer/          # 分析模块
│   │   ├── visual_analyzer.py  # 视觉增强分析
│   │   ├── llm_analyzer.py     # 文案分析
│   │   └── paddleocr_analyzer.py
│   ├── crawler/           # 爬虫模块
│   ├── api/               # API 路由
│   └── .env               # 环境变量
├── web/                   # 前端
│   ├── index.html
│   ├── src/
│   └── package.json
├── data/                  # 数据目录
│   ├── videos/           # 下载的视频文件
│   ├── audio/            # 音频文件
│   └── thumbnails/       # 缩略图
├── docker-compose.yml    # Docker 编排
└── README.md            # 本文档
```

## 🔧 配置说明

### 环境变量
```bash
# 智谱 API
ZHIPU_API_KEY=your_zhipu_api_key
ZHIPU_BASE_URL=https://open.bigmodel.cn/api/paas/v4
ZHIPU_MODEL=GLM-4.1V-Thinking-Flash

# 数据库
DATABASE_URL=postgresql+asyncpg://hotvideo:Hotvideo123!@db:5432/hotvideo

# Redis
REDIS_URL=redis://redis:6379/0

# ASR
SILICONFLOW_API_KEY=your_siliconflow_key
SILICONFLOW_MODEL=FunAudioLLM/SenseVoiceSmall
```

### 爆款阈值配置
```python
# 在 api/main.py 中修改
MIN_VIRAL_SCORE = 5000  # 最低分数阈值
# 计算公式：点赞×0.5 + 评论×0.3 + 分享×0.2 + 收藏×0.1
```

## 🎨 视觉分析流程

### 1. 截帧
```python
# 随机截取 3 帧，避免开头结尾
frames = extract_frames(video_path, num_frames=3)
```

### 2. 视觉分析
```python
result = analyze_frames_with_vision(frames, api_key)
# 返回：人物描述、场景类型、视觉亮点、镜头语言
```

### 3. 爆款洞察
```python
insight = generate_viral_insight(
    visual_result=result,
    title=video_title,
    transcript=video_transcript,
    likes=28000,
    comments=1951,
    shares=17000,
    collects=6857,
    platform="douyin"
)
# 返回：爆点分析、创作提示词、复刻建议
```

## 📈 数据模型

### 视频表结构
```sql
CREATE TABLE douyin_videos (
    video_id VARCHAR(64) PRIMARY KEY,
    description TEXT,           -- 视频文案
    transcript TEXT,            -- 语音转文字
    likes INTEGER,             -- 点赞数
    comments INTEGER,          -- 评论数
    shares INTEGER,            -- 分享数
    collects INTEGER,          -- 收藏数
    
    -- 视觉分析字段
    visual_description TEXT,   -- 画面描述
    scene_types JSON,          -- 场景标签
    visual_highlights JSON,    -- 视觉亮点
    cinematography TEXT,       -- 镜头语言
    
    -- 爆款分析字段
    viral_points JSON,         -- 爆点分析
    visual_hooks JSON,         -- 视觉钩子
    content_hooks JSON,        -- 内容钩子
    emotion_triggers JSON,     -- 情绪触发
    target_audience TEXT,      -- 目标受众
    creation_prompt TEXT,      -- AI 创作提示词
    replication_tips JSON,     -- 复刻建议
    
    frames_analyzed INTEGER    -- 已分析帧数
);
```

## 🎯 使用场景

### 1. 内容创作者
- 分析爆款视频的成功机制
- 获取可直接使用的创作灵感
- 生成 AI 视频提示词，快速制作类似内容

### 2. MCN/运营团队
- 批量分析热门视频
- 建立爆款内容数据库
- 制定内容策略和培训素材

### 3. 产品经理/分析师
- 研究平台内容趋势
- 分析用户偏好和互动模式
- 为产品功能提供数据支持

## 🔄 开发计划

### 近期优化
- [ ] 支持更多视频平台（快手、B站）
- [ ] 增加批量分析功能
- [ ] 优化视觉分析准确度
- [ ] 添加更多 AI 模型支持

### 长期规划
- [ ] 实时热点监控
- [ ] 竞品对比分析
- [ ] 预测模型（哪些内容可能爆）
- [ ] 自动化内容生成

## 🤝 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📄 许可证
MIT License

## 📞 联系方式
- **项目作者**：lostchris123
- **邮箱**：253245270@qq.com
- **GitHub**：https://github.com/lostchris123/hotvideo

---

**✨ 如果这个项目对你有帮助，请点个 Star ⭐️ 支持一下！**