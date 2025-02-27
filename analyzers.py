import os
import re
from config import (
    BINARY_EXTENSIONS,
    IGNORED_NAMES,
    NON_CODE_EXTENSIONS,
    CODE_EXTENSIONS,
    FUNCTION_PATTERNS,
    IGNORED_KEYWORDS
)
import logging
from typing import Dict, Any

def is_binary_file(filename):
    """Check if a file is binary or non-code based on its extension."""
    ext = os.path.splitext(filename)[1].lower()
    
    # Binary extensions
    if ext in BINARY_EXTENSIONS:
        return True
        
    # Documentation and text files that shouldn't be analyzed for functions
    return ext in NON_CODE_EXTENSIONS

def should_ignore_file(name):
    """Check if a file or directory should be ignored."""
    return name in IGNORED_NAMES or name.startswith('.')

def analyze_file_content(file_path):
    """Analyze file content for functions and their descriptions."""
    try:
        # Skip binary and non-code files
        if is_binary_file(file_path):
            return [], 0
            
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Skip files that don't look like actual code files
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in CODE_EXTENSIONS:
            return [], 0
            
        functions = []
        
        # Use patterns for function detection
        for pattern_name, pattern in FUNCTION_PATTERNS.items():
            try:
                matches = re.finditer(pattern, content, re.MULTILINE | re.DOTALL)
                for match in matches:
                    func_name = next(filter(None, match.groups()), None)
                    if not func_name or func_name.lower() in IGNORED_KEYWORDS:
                        continue
                    functions.append((func_name, "Function detected"))
            except re.error as e:
                logging.debug(f"Invalid regex pattern {pattern_name} for {file_path}: {e}")
                continue
            except Exception as e:
                logging.debug(f"Error analyzing pattern {pattern_name} for {file_path}: {e}")
                continue
        
        return functions, len(content.split('\n'))
    except Exception as e:
        print(f"Error analyzing file {file_path}: {e}")
        return [], 0 

def analyze_web_patterns(content: str, rel_path: str, structure: Dict[str, Any], compiled_patterns: Dict[str, Any]) -> None:
    """分析 React/Next.js 特定模式"""
    # 查找接口和类型
    for match in compiled_patterns['common']['interface'].finditer(content):
        structure['patterns']['class_patterns'].append({
            'name': match.group(1),
            'type': 'interface/type',
            'inheritance': match.group(2).strip() if match.group(2) else '',
            'file': rel_path
        })

    # 查找 React 组件
    for match in compiled_patterns['common']['jsx_component'].finditer(content):
        component_name = match.group(1)
        if component_name[0].isupper():  # React 组件以大写字母开头
            structure['patterns']['class_patterns'].append({
                'name': component_name,
                'type': 'react_component',
                'file': rel_path
            })

    # 查找 React hooks
    for hook in re.finditer(compiled_patterns['common']['react_hook'], content):
        structure['patterns']['function_patterns'].append({
            'name': hook.group(0),
            'type': 'react_hook',
            'file': rel_path
        })

    # 查找 Next.js 特定模式
    if any(x in rel_path for x in ['pages/', 'app/']):
        # 检查 Next.js 数据获取方法
        for method in re.finditer(compiled_patterns['common']['next_api'], content):
            structure['patterns']['function_patterns'].append({
                'name': method.group(0),
                'type': 'next_data_fetching',
                'file': rel_path
            })

        # 分析页面/路由结构
        page_match = re.search(compiled_patterns['common']['next_page'], rel_path)
        if page_match:
            structure['patterns']['code_organization'].append({
                'type': 'next_page',
                'route': page_match.group('route'),
                'nested': page_match.group('nested'),
                'file': rel_path
            })

        # 检查布局文件
        if re.search(compiled_patterns['common']['next_layout'], rel_path):
            structure['patterns']['code_organization'].append({
                'type': 'next_layout',
                'file': rel_path
            })

    # 查找 styled-components 模式
    for match in re.finditer(compiled_patterns['common']['styled_component'], content):
        structure['patterns']['code_organization'].append({
            'type': 'styled_component',
            'element': match.group('element') if match.group('element') else 'css',
            'file': rel_path
        })

def analyze_unity_patterns(content: str, rel_path: str, structure: Dict[str, Any], compiled_patterns: Dict[str, Any]) -> None:
    """分析 Unity 特定模式"""
    # 查找 MonoBehaviour 和 ScriptableObject 组件
    for match in compiled_patterns['unity']['component'].finditer(content):
        structure['patterns']['class_patterns'].append({
            'name': match.group(0),
            'type': 'unity_component',
            'file': rel_path
        })

    # 查找 Unity 生命周期方法
    for match in compiled_patterns['unity']['lifecycle'].finditer(content):
        structure['patterns']['function_patterns'].append({
            'name': match.group(0),
            'type': 'unity_lifecycle',
            'file': rel_path
        })

    # 查找 Unity 属性
    for match in compiled_patterns['unity']['attribute'].finditer(content):
        structure['patterns']['code_organization'].append({
            'type': 'unity_attribute',
            'name': match.group(0),
            'parameters': match.group('params') if match.group('params') else '',
            'file': rel_path
        })

    # 查找 Unity 类型
    for match in compiled_patterns['unity']['type'].finditer(content):
        structure['patterns']['class_patterns'].append({
            'name': match.group(0),
            'type': 'unity_type',
            'file': rel_path
        })

    # 查找 Unity 事件
    for match in compiled_patterns['unity']['event'].finditer(content):
        structure['patterns']['code_organization'].append({
            'type': 'unity_event',
            'event_type': match.group('type'),
            'name': match.group('name'),
            'file': rel_path
        })

    # 查找 Unity 序列化字段
    for match in compiled_patterns['unity']['field'].finditer(content):
        structure['patterns']['code_organization'].append({
            'type': 'unity_field',
            'field_type': match.group('type'),
            'name': match.group('name'),
            'file': rel_path
        })

def analyze_directory_patterns(structure: Dict[str, Any], dir_stats: Dict[str, Any]) -> None:
    """分析目录组织模式"""
    for dir_path, stats in dir_stats.items():
        if not dir_path:  # 跳过根目录
            continue
            
        # 分析目录命名约定
        dir_name = os.path.basename(dir_path)
        if dir_name.islower():
            pattern = 'lowercase'
        elif dir_name.isupper():
            pattern = 'uppercase'
        elif '_' in dir_name:
            pattern = 'snake_case'
        elif '-' in dir_name:
            pattern = 'kebab-case'
        else:
            pattern = 'mixed'
            
        # 分析目录用途
        purpose = []
        if any(x in dir_name.lower() for x in ['test', 'spec', 'mock']):
            purpose.append('testing')
        if any(x in dir_name.lower() for x in ['util', 'helper', 'common', 'shared']):
            purpose.append('utilities')
        if any(x in dir_name.lower() for x in ['model', 'entity', 'domain']):
            purpose.append('domain')
        if any(x in dir_name.lower() for x in ['controller', 'handler', 'service']):
            purpose.append('business_logic')
        if any(x in dir_name.lower() for x in ['view', 'template', 'component']):
            purpose.append('presentation')
            
        # 添加目录模式
        structure['patterns']['directory_patterns'].append({
            'path': dir_path,
            'name_pattern': pattern,
            'purpose': purpose,
            'languages': stats['languages'],
            'total_files': stats['total_files'],
            'code_files': stats['code_files'],
            'code_metrics': stats['patterns']
        }) 