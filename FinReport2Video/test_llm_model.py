#!/usr/bin/env python3
"""
LLM 模型测试脚本
CLI 交互式测试不同 LLM 模型的响应情况
"""
import time
import re
from openai import OpenAI

# 默认从 .env 读取配置
try:
    from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    _default_key = LLM_API_KEY
    _default_url = LLM_BASE_URL
    _default_model = LLM_MODEL
except ImportError:
    _default_key = ""
    _default_url = "https://api.openai.com/v1"
    _default_model = ""


def test_model(api_key: str, base_url: str, model: str):
    """测试单个模型"""
    print(f"\n{'='*60}")
    print(f"开始测试模型: {model}")
    print(f"Base URL: {base_url}")
    print(f"{'='*60}\n")
    
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, timeout=120)
    except Exception as e:
        print(f"❌ 客户端初始化失败: {e}")
        return
    
    test_cases = [
        {
            "name": "简单问候",
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 50,
        },
        {
            "name": "金融讲稿生成",
            "messages": [
                {"role": "system", "content": "你是金融播报员，生成口语化流畅讲稿"},
                {"role": "user", "content": "沪深300指数今日收盘3500点，涨幅1.5%，请生成一段30秒播报讲稿"}
            ],
            "max_tokens": 300,
        },
    ]
    
    results = []
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n--- 测试 {i}: {test['name']} ---")
        
        start = time.time()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=test["messages"],
                max_tokens=test["max_tokens"],
                temperature=0.7,
            )
            elapsed = time.time() - start
            
            # 解析响应
            choice = response.choices[0] if response.choices else None
            message = choice.message if choice else None
            
            content = message.content if message else None
            reasoning_content = getattr(message, 'reasoning_content', None) if message else None
            finish_reason = choice.finish_reason if choice else None
            
            # 检测 Markdown
            has_markdown = bool(re.search(r'[#*\-`]', content)) if content else False
            
            # 输出结果
            print(f"✅ 请求成功")
            print(f"\n📊 性能指标:")
            print(f"   响应时间: {elapsed:.2f} 秒")
            if response.usage:
                print(f"   Token 使用: prompt={response.usage.prompt_tokens}, "
                      f"completion={response.usage.completion_tokens}, "
                      f"total={response.usage.total_tokens}")
            
            print(f"\n📋 响应字段:")
            print(f"   content: {'有内容' if content else '❌ 为空'}")
            print(f"   reasoning_content: {'有内容' if reasoning_content else '无'}")
            print(f"   finish_reason: {finish_reason}")
            print(f"   包含 Markdown: {'是' if has_markdown else '否'}")
            
            print(f"\n📝 响应内容:")
            if content:
                # 显示前 200 字符
                display = content[:200] + "..." if len(content) > 200 else content
                print(f"   长度: {len(content)} 字")
                print(f"   内容: {display}")
            elif reasoning_content:
                display = reasoning_content[:200] + "..." if len(reasoning_content) > 200 else reasoning_content
                print(f"   ⚠️ content 为空，但 reasoning_content 有内容")
                print(f"   长度: {len(reasoning_content)} 字")
                print(f"   内容: {display}")
            else:
                print(f"   ❌ 无内容")
            
            results.append({
                "name": test["name"],
                "success": True,
                "elapsed": elapsed,
                "has_content": bool(content),
                "has_markdown": has_markdown,
            })
            
        except Exception as e:
            elapsed = time.time() - start
            error_msg = str(e)
            print(f"❌ 请求失败")
            print(f"   错误: {error_msg[:100]}")
            results.append({
                "name": test["name"],
                "success": False,
                "error": error_msg[:100],
            })
    
    # 汇总
    print(f"\n{'='*60}")
    print("📈 测试汇总")
    print(f"{'='*60}")
    success_count = sum(1 for r in results if r["success"])
    print(f"成功: {success_count}/{len(results)}")
    
    if success_count > 0:
        avg_time = sum(r["elapsed"] for r in results if r["success"]) / success_count
        print(f"平均响应时间: {avg_time:.2f} 秒")
    
    for r in results:
        status = "✅" if r["success"] else "❌"
        extra = f"{r['elapsed']:.2f}s" if r["success"] else r.get("error", "")[:30]
        print(f"  {status} {r['name']}: {extra}")


def main():
    print("="*60)
    print("LLM 模型测试工具")
    print("="*60)
    print("请输入模型配置信息（直接回车使用默认值）\n")
    
    # 读取用户输入
    api_key = input(f"API Key [{_default_key[:10]}...]: ").strip()
    if not api_key:
        api_key = _default_key
    if not api_key:
        print("❌ API Key 不能为空")
        return
    
    base_url = input(f"Base URL [{_default_url}]: ").strip()
    if not base_url:
        base_url = _default_url
    
    model = input(f"Model 名称 [{_default_model}]: ").strip()
    if not model:
        model = _default_model
    if not model:
        print("❌ Model 名称不能为空")
        return
    
    # 执行测试
    test_model(api_key, base_url, model)
    
    # 询问是否继续测试其他模型
    while True:
        print()
        choice = input("是否测试其他模型？(y/n): ").strip().lower()
        if choice != 'y':
            break
        
        print()
        api_key = input("API Key (回车保持不变): ").strip() or api_key
        base_url = input("Base URL (回车保持不变): ").strip() or base_url
        model = input("Model 名称: ").strip()
        if not model:
            print("❌ Model 名称不能为空")
            continue
        
        test_model(api_key, base_url, model)
    
    print("\n测试结束")


if __name__ == "__main__":
    main()
