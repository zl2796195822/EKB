"""预设远程 LLM 服务商目录。

包含国内外主流大模型平台，以及兼容 OpenAI 协议的自建服务。
每个预设提供默认配置，用户创建实例时可覆盖。
"""

from __future__ import annotations

PRESET_PROVIDERS: list[dict] = [
    {
        "id": "deepseek",
        "name": "深度求索 DeepSeek",
        "description": "深度求索推出的大语言模型 API，提供推理模型和通用对话模型。2026 官方在售仅 2 款：DeepSeek Chat（通用对话）和 DeepSeek Reasoner（深度推理）。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                # DeepSeek 2026 官方最新 OpenAI 格式端点：https://api.deepseek.com
                # （OpenAI SDK 会自动拼接 /chat/completions；旧的 /v1 仍兼容但不再推荐）
                "baseUrl": "https://api.deepseek.com",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        # 2026 Q2 起：GUI 展示只保留官方白名单在售模型，不再允许 API 返回的 v3/v4-pro/v4-flash 等历史灰度模型混入
        "model_list_source": "catalog+api",
        "catalog_models": [
            {
                "id": "deepseek-chat",
                "name": "DeepSeek Chat",
                "model_type": "chat",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "capabilities": {"chat": True, "tool_use": True},
                "input_price": "0.14",
                "output_price": "0.28",
            },
            {
                "id": "deepseek-reasoner",
                "name": "DeepSeek Reasoner",
                "model_type": "reasoning",
                "context_window": 65536,
                "max_output_tokens": 8192,
                "capabilities": {"reasoning": True, "chat": True, "tool_use": True},
                "input_price": "0.55",
                "output_price": "2.19",
            },
        ],
        "websites": {
            "official": "https://www.deepseek.com",
            "docs": "https://platform.deepseek.com/api-docs",
            "apiKey": "https://platform.deepseek.com/api_keys",
            "models": "https://platform.deepseek.com/api-docs/pricing",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "cherryin",
        "name": "CherryIN",
        "description": "CherryIN 大模型聚合平台，兼容 OpenAI 协议。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.cherryin.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.cherryin.com",
            "docs": "https://docs.cherryin.com",
            "apiKey": "https://www.cherryin.com/apikey",
            "models": "https://www.cherryin.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "silicon",
        "name": "硅基流动 SiliconFlow",
        "description": "硅基流动提供的一站式大模型推理服务，支持众多开源和商业模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.siliconflow.cn/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.siliconflow.cn",
            "docs": "https://docs.siliconflow.cn",
            "apiKey": "https://cloud.siliconflow.cn/apikeys",
            "models": "https://docs.siliconflow.cn/model-support",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "aihubmix",
        "name": "AiHubMix",
        "description": "AiHubMix 大模型聚合平台，集成全球主流大模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://aihubmix.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.aihubmix.com",
            "docs": "https://docs.aihubmix.com",
            "apiKey": "https://www.aihubmix.com/user/keys",
            "models": "https://www.aihubmix.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "zhipu",
        "name": "智谱 AI",
        "description": "智谱 AI 推出的大语言模型 API，包括 GLM 系列模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.zhipuai.cn",
            "docs": "https://open.bigmodel.cn/dev/howuse/introduction",
            "apiKey": "https://open.bigmodel.cn/usercenter/apikeys",
            "models": "https://open.bigmodel.cn/pricing",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "moonshot",
        "name": "月之暗面 Moonshot",
        "description": "Moonshot AI 推出的 Kimi 大模型 API，支持超长上下文。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.moonshot.cn/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.moonshot.cn",
            "docs": "https://platform.moonshot.cn/docs",
            "apiKey": "https://platform.moonshot.cn/console/api-keys",
            "models": "https://platform.moonshot.cn/docs/intro#模型列表",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "dashscope",
        "name": "阿里百炼 DashScope",
        "description": "阿里云推出的通义大模型 API 服务，包括 Qwen 系列模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://bailian.console.aliyun.com",
            "docs": "https://help.aliyun.com/zh/model-studio",
            "apiKey": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
            "models": "https://help.aliyun.com/zh/model-studio/getting-started/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "hunyuan",
        "name": "腾讯混元",
        "description": "腾讯云推出的混元大模型 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.hunyuan.cloud.tencent.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://cloud.tencent.com/product/hunyuan",
            "docs": "https://cloud.tencent.com/document/product/1729",
            "apiKey": "https://console.cloud.tencent.com/hunyuan/apiKey",
            "models": "https://cloud.tencent.com/document/product/1729/104753",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "doubao",
        "name": "字节豆包",
        "description": "字节跳动推出的豆包大模型平台 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.volcengine.com/product/dp-mmm",
            "docs": "https://www.volcengine.com/docs/82379",
            "apiKey": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
            "models": "https://www.volcengine.com/docs/82379/1298454",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "minimax",
        "name": "MiniMax",
        "description": "MiniMax 推出的大模型 API 服务，包括 ABAB 系列模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.minimax.chat/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.minimaxi.com",
            "docs": "https://platform.minimaxi.com/document/Announcing",
            "apiKey": "https://platform.minimaxi.com/user-center/basic-information",
            "models": "https://platform.minimaxi.com/document/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "baichuan",
        "name": "百川智能 Baichuan",
        "description": "百川智能推出的大模型 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.baichuan-ai.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.baichuan-ai.com",
            "docs": "https://platform.baichuan-ai.com/docs",
            "apiKey": "https://platform.baichuan-ai.com/console/apikey",
            "models": "https://platform.baichuan-ai.com/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "yi",
        "name": "零一万物 Yi",
        "description": "零一万物推出的 Yi 系列大模型 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.lingyiwanwu.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.lingyiwanwu.com",
            "docs": "https://platform.lingyiwanwu.com/docs",
            "apiKey": "https://platform.lingyiwanwu.com/apikeys",
            "models": "https://platform.lingyiwanwu.com/docs#模型列表",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "description": "OpenAI 官方 API，提供 GPT 系列模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.openai.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://openai.com",
            "docs": "https://platform.openai.com/docs",
            "apiKey": "https://platform.openai.com/api-keys",
            "models": "https://platform.openai.com/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": True,
            "serviceTier": True,
            "verbosity": True,
        },
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "description": "Anthropic 官方 API，提供 Claude 系列模型。",
        "default_chat_endpoint": "anthropic-messages",
        "endpoint_configs": {
            "anthropic-messages": {
                "baseUrl": "https://api.anthropic.com/v1",
                "adapterFamily": "anthropic",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.anthropic.com",
            "docs": "https://docs.anthropic.com",
            "apiKey": "https://console.anthropic.com/settings/keys",
            "models": "https://docs.anthropic.com/claude/docs/models-overview",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": True,
        },
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "description": "OpenRouter 聚合平台，接入全球数百种大模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://openrouter.ai/api/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://openrouter.ai",
            "docs": "https://openrouter.ai/docs",
            "apiKey": "https://openrouter.ai/keys",
            "models": "https://openrouter.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": True,
            "verbosity": False,
        },
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "description": "Google 推出的 Gemini 大模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://ai.google.dev",
            "docs": "https://ai.google.dev/gemini-api/docs",
            "apiKey": "https://aistudio.google.com/app/apikey",
            "models": "https://ai.google.dev/gemini-api/docs/models/gemini",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "groq",
        "name": "Groq",
        "description": "Groq 推出的高速推理 API，支持多种开源模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.groq.com/openai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://groq.com",
            "docs": "https://console.groq.com/docs",
            "apiKey": "https://console.groq.com/keys",
            "models": "https://console.groq.com/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "302ai",
        "name": "302.AI",
        "description": "302.AI 大模型聚合平台，支持多种主流模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.302.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://302.ai",
            "docs": "https://docs.302.ai",
            "apiKey": "https://app.302.ai/user/apiKey",
            "models": "https://docs.302.ai/model",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "ppio",
        "name": "PPUO",
        "description": "PPUO 边缘云推理服务，提供高性价比的大模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.ppinfra.com/v1/openai",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.ppio.cloud",
            "docs": "https://docs.ppio.cloud",
            "apiKey": "https://cloud.ppio.xyz/settings/api-keys",
            "models": "https://docs.ppio.cloud/zh/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "qiniu",
        "name": "七牛云 Qiniu",
        "description": "七牛云推出的大模型 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.qiniu.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.qiniu.com",
            "docs": "https://developer.qiniu.com",
            "apiKey": "https://portal.qiniu.com/user/key",
            "models": "https://www.qiniu.com/products/ai-model",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "aionly",
        "name": "唯一 AI",
        "description": "唯一 AI 大模型聚合平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.aionly.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.aionly.com",
            "docs": "https://docs.aionly.com",
            "apiKey": "https://www.aionly.com/console/api-keys",
            "models": "https://www.aionly.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "burncloud",
        "name": "BurnCloud",
        "description": "BurnCloud 大模型 API 服务平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.burn.cloud/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://burn.cloud",
            "docs": "https://docs.burn.cloud",
            "apiKey": "https://burn.cloud/dashboard/keys",
            "models": "https://burn.cloud/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "longcat",
        "name": "LongCat",
        "description": "LongCat 长猫 AI API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.longcat.cloud/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.longcat.cloud",
            "docs": "https://docs.longcat.cloud",
            "apiKey": "https://platform.longcat.cloud/api-keys",
            "models": "https://www.longcat.cloud/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "description": "Mistral AI 官方 API，提供 Mistral 系列模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.mistral.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://mistral.ai",
            "docs": "https://docs.mistral.ai",
            "apiKey": "https://console.mistral.ai/api-keys",
            "models": "https://docs.mistral.ai/getting-started/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "together",
        "name": "Together AI",
        "description": "Together AI 开源大模型推理平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.together.xyz/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.together.ai",
            "docs": "https://docs.together.ai",
            "apiKey": "https://api.together.xyz/settings/api-keys",
            "models": "https://docs.together.ai/docs/chat-models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "description": "Fireworks AI 大模型推理平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.fireworks.ai/inference/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://fireworks.ai",
            "docs": "https://readme.fireworks.ai",
            "apiKey": "https://fireworks.ai/account/api-keys",
            "models": "https://fireworks.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "perplexity",
        "name": "Perplexity",
        "description": "Perplexity API 提供远程对话模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.perplexity.ai",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.perplexity.ai",
            "docs": "https://docs.perplexity.ai",
            "apiKey": "https://www.perplexity.ai/settings/api",
            "models": "https://docs.perplexity.ai/docs/model-cards",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "nvidia",
        "name": "Nvidia NIM",
        "description": "NVIDIA NIM 微服务推理 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://integrate.api.nvidia.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.nvidia.com/en-us/ai",
            "docs": "https://docs.nvidia.com/nim",
            "apiKey": "https://build.nvidia.com/nim",
            "models": "https://build.nvidia.com/explore/discover",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "grok",
        "name": "Grok xAI",
        "description": "xAI 推出的 Grok 大模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.x.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://x.ai",
            "docs": "https://docs.x.ai",
            "apiKey": "https://console.x.ai",
            "models": "https://docs.x.ai/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "hyperbolic",
        "name": "Hyperbolic",
        "description": "Hyperbolic 大模型推理平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.hyperbolic.xyz/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.hyperbolic.ai",
            "docs": "https://docs.hyperbolic.ai",
            "apiKey": "https://app.hyperbolic.xyz/settings",
            "models": "https://www.hyperbolic.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "poe",
        "name": "Poe",
        "description": "Poe 平台 API，接入多种大模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.poe.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://poe.com",
            "docs": "https://developer.poe.com",
            "apiKey": "https://poe.com/api_key",
            "models": "https://developer.poe.com/server-bots/accessing-other-bots-on-poe",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "huggingface",
        "name": "HuggingFace",
        "description": "HuggingFace Inference API，提供大量开源模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api-inference.huggingface.co/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://huggingface.co",
            "docs": "https://huggingface.co/docs",
            "apiKey": "https://huggingface.co/settings/tokens",
            "models": "https://huggingface.co/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "gateway",
        "name": "Vercel AI Gateway",
        "description": "Vercel AI Gateway 统一接入多种大模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://gateway.ai.cloudflare.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://vercel.com",
            "docs": "https://vercel.com/docs/ai",
            "apiKey": "https://vercel.com/dashboard",
            "models": "https://sdk.vercel.ai/providers",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "cerebras",
        "name": "Cerebras",
        "description": "Cerebras 超高速推理 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.cerebras.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.cerebras.net",
            "docs": "https://inference-docs.cerebras.ai",
            "apiKey": "https://cloud.cerebras.ai/platform/org/0/api_keys",
            "models": "https://inference-docs.cerebras.ai/introduction",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "mimo",
        "name": "小米 MiMo",
        "description": "小米推出的 MiMo 大模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.mimo.mi.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://mimo.mi.com",
            "docs": "https://dev.mimo.mi.com",
            "apiKey": "https://mimo.mi.com/console/apikey",
            "models": "https://dev.mimo.mi.com/docs/model",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "zai",
        "name": "zai",
        "description": "ZAI 大模型 API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.zai.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.zai.ai",
            "docs": "https://docs.zai.ai",
            "apiKey": "https://www.zai.ai/console/keys",
            "models": "https://www.zai.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "modelscope",
        "name": "ModelScope 魔搭",
        "description": "阿里达摩院推出的 ModelScope 魔搭社区模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api-inference.modelscope.cn/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://modelscope.cn",
            "docs": "https://modelscope.cn/docs",
            "apiKey": "https://modelscope.cn/my/myaccesstoken",
            "models": "https://modelscope.cn/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "xirang",
        "name": "Xirang 息壤",
        "description": "中国电信息壤大模型服务平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.xirang.telelist.com.cn/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.chinatelecom.com.cn",
            "docs": "",
            "apiKey": "",
            "models": "",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "tencent-cloud-ti",
        "name": "腾讯云 TI",
        "description": "腾讯云 TI 大模型一站式平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.tencent-cloud-ti.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://cloud.tencent.com/product/ti",
            "docs": "https://cloud.tencent.com/document/product/1357",
            "apiKey": "https://console.cloud.tencent.com/ti",
            "models": "https://cloud.tencent.com/document/product/1357/105576",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "tokenhub",
        "name": "TokenHub",
        "description": "TokenHub 大模型 API 聚合平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.tokenhub.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.tokenhub.com",
            "docs": "https://docs.tokenhub.com",
            "apiKey": "https://www.tokenhub.com/user/keys",
            "models": "https://www.tokenhub.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "baidu-cloud",
        "name": "百度云",
        "description": "百度智能云千帆大模型平台，提供文心一言等模型。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://qianfan.baidubce.com/v2",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://cloud.baidu.com/product/wenxinworkshop.html",
            "docs": "https://cloud.baidu.com/doc/WENXINWORKSHOP",
            "apiKey": "https://console.bce.baidu.com/qianfan/ais/console/applicationConsole/application",
            "models": "https://cloud.baidu.com/doc/WENXINWORKSHOP/s/xlmok5if7",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "aws-bedrock",
        "name": "AWS Bedrock",
        "description": "AWS Bedrock 托管式大模型服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://bedrock-runtime.us-east-1.amazonaws.com",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://aws.amazon.com/bedrock",
            "docs": "https://docs.aws.amazon.com/bedrock",
            "apiKey": "https://console.aws.amazon.com/iamv2/home#/security_credentials",
            "models": "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "voyageai",
        "name": "VoyageAI",
        "description": "Voyage AI 专业 Embedding 和 Rerank 模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.voyageai.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.voyageai.com",
            "docs": "https://docs.voyageai.com",
            "apiKey": "https://dash.voyageai.com/api-keys",
            "models": "https://docs.voyageai.com/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": False,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "ocoolai",
        "name": "OpenCool AI",
        "description": "OpenCool AI 大模型聚合平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.ocoolai.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.ocoolai.com",
            "docs": "https://docs.ocoolai.com",
            "apiKey": "https://www.ocoolai.com/console/keys",
            "models": "https://www.ocoolai.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "alayanew",
        "name": "AlayaNew",
        "description": "AlayaNew 大模型 AI 服务平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.alayanew.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.alayanew.com",
            "docs": "https://docs.alayanew.com",
            "apiKey": "https://www.alayanew.com/keys",
            "models": "https://www.alayanew.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "dmxapi",
        "name": "DMXAPI",
        "description": "DMXAPI 大模型聚合服务平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://www.dmxapi.cn/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.dmxapi.cn",
            "docs": "https://docs.dmxapi.cn",
            "apiKey": "https://www.dmxapi.cn/user/keys",
            "models": "https://www.dmxapi.cn/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "ph8",
        "name": "PH8",
        "description": "PH8 大模型 API 服务平台。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.ph8.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://ph8.ai",
            "docs": "https://docs.ph8.ai",
            "apiKey": "https://ph8.ai/keys",
            "models": "https://ph8.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "sophnet",
        "name": "SophNet",
        "description": "SophNet 大模型 AI API 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.sophnet.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.sophnet.ai",
            "docs": "https://docs.sophnet.ai",
            "apiKey": "https://www.sophnet.ai/keys",
            "models": "https://www.sophnet.ai/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "infini",
        "name": "Infini 无问芯穹",
        "description": "无问芯穹推出的大模型推理平台 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://cloud.infini-ai.com/nvidia/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://www.infini-ai.com",
            "docs": "https://docs.infini-ai.com",
            "apiKey": "https://cloud.infini-ai.com/access-key",
            "models": "https://cloud.infini-ai.com/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "jina",
        "name": "Jina AI",
        "description": "Jina AI 提供 Embedding、Rerank 以及多模态模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.jina.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://jina.ai",
            "docs": "https://jina.ai/developers",
            "apiKey": "https://jina.ai/settings/api-keys",
            "models": "https://jina.ai/developers/#models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": False,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "github",
        "name": "GitHub Models",
        "description": "GitHub Models 市场的推理 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://models.inference.ai.azure.com",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://github.com/marketplace/models",
            "docs": "https://docs.github.com/en/github-models",
            "apiKey": "https://github.com/settings/tokens",
            "models": "https://github.com/marketplace/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "copilot",
        "name": "GitHub Copilot",
        "description": "GitHub Copilot 代码助手相关模型 API。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.githubcopilot.com",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://github.com/features/copilot",
            "docs": "https://docs.github.com/en/copilot",
            "apiKey": "https://github.com/settings/tokens",
            "models": "",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": True,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "claude-code",
        "name": "Claude Code",
        "description": "Claude Code 本地命令行编码助手（基于 Anthropic Claude）。",
        "default_chat_endpoint": "anthropic-messages",
        "endpoint_configs": {
            "anthropic-messages": {
                "baseUrl": "https://api.anthropic.com/v1",
                "adapterFamily": "anthropic",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://www.anthropic.com/claude-code",
            "docs": "https://docs.anthropic.com/claude-code",
            "apiKey": "https://console.anthropic.com/settings/keys",
            "models": "",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": True,
        },
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex",
        "description": "OpenAI Codex 代码模型系列。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.openai.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://openai.com",
            "docs": "https://platform.openai.com/docs",
            "apiKey": "https://platform.openai.com/api-keys",
            "models": "https://platform.openai.com/docs/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": True,
            "serviceTier": True,
            "verbosity": True,
        },
    },
    {
        "id": "grok-cli",
        "name": "Grok CLI",
        "description": "Grok CLI 命令行助手（xAI Grok）。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://api.x.ai/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "registry",
        "websites": {
            "official": "https://x.ai",
            "docs": "https://docs.x.ai",
            "apiKey": "https://console.x.ai",
            "models": "",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "vertexai",
        "name": "Vertex AI",
        "description": "Google Cloud Vertex AI 托管式大模型服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://us-central1-aiplatform.googleapis.com/v1",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://cloud.google.com/vertex-ai",
            "docs": "https://cloud.google.com/vertex-ai/docs",
            "apiKey": "https://console.cloud.google.com/apis/credentials",
            "models": "https://cloud.google.com/vertex-ai/generative-ai/docs/learn/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": False,
            "serviceTier": False,
            "verbosity": False,
        },
    },
    {
        "id": "azure-openai",
        "name": "Azure OpenAI",
        "description": "Azure 托管的 OpenAI 服务。",
        "default_chat_endpoint": "openai-chat-completions",
        "endpoint_configs": {
            "openai-chat-completions": {
                "baseUrl": "https://{resource-name}.openai.azure.com/openai/deployments/{deployment-id}",
                "adapterFamily": "openai",
            }
        },
        "auth_type": "api-key",
        "auth_optional": False,
        "model_list_source": "api",
        "websites": {
            "official": "https://azure.microsoft.com/en-us/products/ai-services/openai-service",
            "docs": "https://learn.microsoft.com/en-us/azure/ai-services/openai",
            "apiKey": "https://portal.azure.com/#view/Microsoft_Azure_ProjectOxford/OpenAIBlade",
            "models": "https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models",
        },
        "api_features": {
            "arrayContent": True,
            "streamOptions": True,
            "developerRole": True,
            "serviceTier": True,
            "verbosity": True,
        },
    },
]

_LOCAL_PROVIDER_KEYS = {
    "ollama",
    "ollama-chat",
    "lmstudio",
    "lm-studio",
    "new-api",
    "newapi",
    "gpustack",
    "gpu-stack",
    "ovms",
    "openvino",
    "openvino-model-server",
    "opencode",
    "opencode-go",
}


def is_local_provider_key(provider_key: str | None) -> bool:
    """Return whether a provider key identifies a local inference service."""
    return isinstance(provider_key, str) and provider_key.strip().lower() in _LOCAL_PROVIDER_KEYS


def get_preset_provider(provider_key: str) -> dict | None:
    """根据 provider_key 获取预设服务商配置。"""
    if is_local_provider_key(provider_key):
        return None
    for provider in PRESET_PROVIDERS:
        if provider["id"] == provider_key and not is_local_provider_key(provider["id"]):
            return provider
    return None


def list_preset_providers() -> list[dict]:
    """列出全部远程预设服务商，不暴露本地推理入口。"""
    return [
        provider for provider in PRESET_PROVIDERS if not is_local_provider_key(provider.get("id"))
    ]
