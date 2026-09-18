---
name: devin-notify-routing
description: "双 Hermes 通知路由规则：assistant 与 majordomo 的任务完成通知必须分流到各自飞书会话。用于为 Devin 任务写系统提示词时保证通知不发错对象。"
version: 1.0.0
---

# Devin 通知路由规则

## 核心原则
系统有两个 Hermes 实例，必须严格分流通知：
- **assistant**（助手 Vela）— 当前对话的这个 Hermes
- **majordomo**（大管家 Vela）— 另一个 Hermes 实例

**通知绝不能混发。每个 Hermes 只看自己的通知。**

## 分流机制

### 通知文件命名
```
/home/haa/.skillhome/notifications/
  ├── assistant-{task_type}-{timestamp}.json   ← assistant 专用
  └── majordomo-{task_type}-{timestamp}.json  ← majordomo 专用
```

### 飞书 Channel
两个 Hermes 的飞书 chat_id 已配置在 `.env`：
- `FEISHU_ASSISTANT_CHANNEL` → assistant 的飞书会话
- `FEISHU_MAJORDOMO_CHANNEL` → majordomo 的飞书会话
- `FEISHU_HOME_CHANNEL` → fallback（不应再使用）

### 通知 JSON 格式
```json
{
  "source": "assistant|majordomo",
  "task_type": "任务类型",
  "timestamp": "ISO 时间戳",
  "status": "success|failed",
  "task": "任务描述",
  "duration_seconds": 123,
  "result": {
    "summary": "简短总结",
    "report": { ... }
  },
  "error": "失败时的错误信息",
  "feishu_sent": true|false
}
```

### 飞书消息格式
```
✅ SkillHome 任务完成
任务：{task_type}
负责人：{source}
耗时：{duration}s
结果：{summary}
```

失败时：
```
❌ SkillHome 任务失败
任务：{task_type}
负责人：{source}
错误：{error}
```

## Devin Prompt 模板
给 Devin 的 prompt 开头固定加入：

```markdown
## 通知路由
你属于 Hermes 实例：{assistant|majordomo}
任务完成后调用 notify(source="{assistant|majordomo}", task_type="...", status="success|failed", result={...})
```

## 错误处理
- 飞书发送失败：只记 WARN，不阻塞主任务
- 无凭证时：静默跳过发送，只写 JSON
- 永不 fallback 到对方 channel

## 验证
```bash
cd /home/haa/sites/skillhome
python3 bin/skillhome.py {command} --source {assistant|majordomo}
ls -la /home/haa/.skillhome/notifications/
```

## 注意
- `.env` 里的 channel 映射不进 git
- 此 skill 是路由规则的唯一权威来源
- 新增 Hermes 实例时，先在 `.env` 配 channel，再更新此 skill
