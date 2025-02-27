import os
import json
from typing import Dict, Any, List, Set
from datetime import datetime
import google.generativeai as genai
import re
from rules_analyzer import RulesAnalyzer
from dotenv import load_dotenv
import time
from functools import wraps
import tqdm
import threading
import signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import fnmatch
import pathspec

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("操作超时")

# 设置超时处理
signal.signal(signal.SIGALRM, timeout_handler)

def show_progress_spinner():
    """显示加载动画"""
    spinner = ['⣾', '⣽', '⣻', '⢿', '⡿', '⣟', '⣯', '⣷']
    i = 0
    while True:
        print(f"\r{spinner[i]} 处理中...", end='')
        i = (i + 1) % len(spinner)
        time.sleep(0.1)

def with_progress(desc: str):
    """进度条装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            spinner_thread = threading.Thread(target=show_progress_spinner)
            spinner_thread.daemon = True
            print(f"\n🔄 {desc}")
            spinner_thread.start()
            try:
                result = func(*args, **kwargs)
                print("\r✅ 完成" + " " * 20)
                return result
            except Exception as e:
                print("\r❌ 失败" + " " * 20)
                raise
            finally:
                spinner_thread.do_run = False
                spinner_thread.join(0)
        return wrapper
    return decorator

def retry_on_429(max_retries=3, delay=2):
    """带进度的重试装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if '429' in str(e) and retries < max_retries - 1:
                        wait_time = delay * (2 ** retries)
                        print(f"\r⚠️ 触发限流，{wait_time} 秒后重试... ({retries + 1}/{max_retries})")
                        time.sleep(wait_time)
                        retries += 1
                        continue
                    raise
            return func(*args, **kwargs)
        return wrapper
    return decorator

class RulesGenerator:
    # Common regex patterns for all languages
    # Language groups:
    # python: Python
    # web: JavaScript, TypeScript, Java, Ruby
    # system: C/C++, C#, PHP, Swift, Objective-C
    PATTERNS = {
        'import': {
            'python': r'^(?:from\s+(?P<module>[a-zA-Z0-9_\.]+)\s+import\s+(?P<imports>[^#\n]+)|import\s+(?P<module2>[a-zA-Z0-9_\.]+(?:\s*,\s*[a-zA-Z0-9_\.]+)*))(?:\s*#[^\n]*)?$',
            'web': r'(?:' + '|'.join([
                r'import\s+.*?from\s+[\'"](?P<module>[^\'\"]+)[\'"]',  # ES6 import
                r'require\s*\([\'"](?P<module2>[^\'\"]+)[\'"]\)',      # CommonJS require
                r'import\s+(?:static\s+)?(?P<module3>[a-zA-Z0-9_\.]+(?:\.[*])?)',  # Java/TypeScript import
                r'require\s+[\'"](?P<module4>[^\'\"]+)[\'"]',          # Ruby require
                r'import\s+[\'"](?P<module5>[^\'\"]+)[\'"]'            # Plain import
            ]) + ')',
            'system': r'(?:' + '|'.join([
                r'#include\s*[<"](?P<module>[^>"]+)[>"]',              # C/C++ include
                r'using\s+(?:static\s+)?(?P<module2>[a-zA-Z0-9_\.]+)\s*;',  # C# using
                r'namespace\s+(?P<module3>[a-zA-Z0-9_\\]+)',          # Namespace
                r'import\s+(?P<module4>[^\n;]+)\s*;?',                # Swift/Kotlin import
                r'#import\s*[<"](?P<module5>[^>"]+)[>"]'              # Objective-C import
            ]) + ')',
        },
        'class': {
            'python': r'(?:@\w+(?:\(.*?\))?\s+)*class\s+(?P<name>\w+)(?:\((?P<base>[^)]+)\))?\s*:(?:\s*[\'"](?P<docstring>[^\'"]*)[\'"])?',
            'web': r'(?:' + '|'.join([
                r'(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)(?:\s*(?:extends|implements)\s+(?P<base>[^{<]+))?(?:\s*<[^>]+>)?\s*{',  # Standard class
                r'(?:export\s+)?(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*class(?:\s+extends\s+(?P<base2>[^{]+))?\s*{',  # Class expression
                r'(?:export\s+)?class\s+(?P<name3>\w+)\s*(?:<[^>]+>)?\s*(?:extends|implements)\s+(?P<base3>[^{]+)?\s*{'  # Generic class
            ]) + ')',
            'system': r'(?:' + '|'.join([
                r'(?:(?:public|private|protected|internal|friend)\s+)*(?:abstract\s+)?(?:partial\s+)?(?:sealed\s+)?(?:class|struct|enum|union|@interface|@implementation)\s+(?P<name>\w+)(?:\s*(?::\s*|extends\s+|implements\s+)(?P<base>[^{;]+))?(?:\s*{)?',  # C++/C#/Java class
                r'(?:@interface|@implementation)\s+(?P<name2>\w+)(?:\s*:\s*(?P<base2>[^{]+))?\s*{?'  # Objective-C interface/implementation
            ]) + ')',
        },
        'function': {
            'python': r'(?:@\w+(?:\(.*?\))?\s+)*def\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?:\s*->\s*(?P<return>[^:#]+))?\s*:(?:\s*[\'"](?P<docstring>[^\'"]*)[\'"])?',
            'web': r'(?:' + '|'.join([
                r'(?:export\s+)?(?:async\s+)?function\s*(?P<name>\w+)\s*(?:<[^>]+>)?\s*\((?P<params>[^)]*)\)(?:\s*:\s*(?P<return>[^{=]+))?\s*{',  # Standard function
                r'(?:export\s+)?(?:const|let|var)\s+(?P<name2>\w+)\s*=\s*(?:async\s+)?(?:function\s*\*?|\([^)]*\)\s*=>)',  # Function expression/arrow
                r'(?:public|private|protected)?\s*(?:static\s+)?(?:async\s+)?(?P<name3>\w+)\s*\((?P<params2>[^)]*)\)(?:\s*:\s*(?P<return2>[^{;]+))?\s*{?'  # Method
            ]) + ')',
            'system': r'(?:' + '|'.join([
                r'(?:(?:public|private|protected|internal|friend)\s+)*(?:static\s+)?(?:virtual\s+)?(?:override\s+)?(?:async\s+)?(?:[\w:]+\s+)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?:\s*(?:const|override|final|noexcept))?\s*(?:{\s*)?',  # C++/C#/Java method
                r'[-+]\s*\((?P<return>[^)]+)\)(?P<name2>\w+)(?::\s*\((?P<paramtype>[^)]+)\)(?P<param>\w+))*'  # Objective-C method
            ]) + ')',
        },
        'common': {
            'method': r'(?:(?:public|private|protected)\s+)?(?:static\s+)?(?:async\s+)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)(?:\s*:\s*(?P<return>[^{]+))?\s*{',
            'variable': r'(?:(?:public|private|protected)\s+)?(?:static\s+)?(?:const|let|var|final)\s+(?P<name>\w+)\s*(?::\s*(?P<type>[^=;]+))?\s*=\s*(?P<value>[^;]+)',
            'error': r'try\s*{(?:[^{}]|{[^{}]*})*}\s*catch\s*\((?P<error>\w+)(?:\s*:\s*(?P<type>[^)]+))?\)',
            'interface': r'(?:export\s+)?interface\s+(?P<name>\w+)(?:\s+extends\s+(?P<base>[^{]+))?\s*{(?:[^{}]|{[^{}]*})*}',
            'jsx_component': r'<(?P<name>[A-Z]\w*)(?:\s+(?:(?!\/>)[^>])+)?>',
            'react_hook': r'\buse[A-Z]\w+\b(?=\s*\()',
            'next_api': r'export\s+(?:async\s+)?function\s+(?:getStaticProps|getStaticPaths|getServerSideProps)\s*\(',
            'next_page': r'(?:pages|app)/(?!_)[^/]+(?:/(?!_)[^/]+)*\.(?:js|jsx|ts|tsx)$',
            'next_layout': r'(?:layout|page|loading|error|not-found)\.(?:js|jsx|ts|tsx)$',
            'next_middleware': r'middleware\.(?:js|jsx|ts|tsx)$',
            'styled_component': r'(?:const\s+)?(?P<name>\w+)\s*=\s*styled(?:\.(?P<element>\w+)|(?:\([\w.]+\)))`[^`]*`'
        },
        'unity': {
            'component': r'(?:public\s+)?class\s+\w+\s*:\s*(?:MonoBehaviour|ScriptableObject|EditorWindow)',
            'lifecycle': r'(?:private\s+|protected\s+|public\s+)?(?:virtual\s+)?(?:override\s+)?void\s+(?:Awake|Start|Update|FixedUpdate|LateUpdate|OnEnable|OnDisable|OnDestroy|OnTriggerEnter|OnTriggerExit|OnCollisionEnter|OnCollisionExit|OnMouseDown|OnMouseUp|OnGUI)\s*\([^)]*\)',
            'attribute': r'\[\s*(?:SerializeField|Header|Tooltip|Range|RequireComponent|ExecuteInEditMode|CreateAssetMenu|MenuItem)(?:\s*\(\s*(?P<params>[^)]+)\s*\))?\s*\]',
            'type': r'\b(?:GameObject|Transform|Rigidbody|Collider|AudioSource|Camera|Light|Animator|ParticleSystem|Canvas|Image|Text|Button|Vector[23]|Quaternion)\b',
            'event': r'(?:public\s+|private\s+|protected\s+)?UnityEvent\s*<\s*(?P<type>[^>]*)\s*>\s+(?P<name>\w+)',
            'field': r'(?:public\s+|private\s+|protected\s+|internal\s+)?(?:\[SerializeField\]\s*)?(?P<type>\w+(?:<[^>]+>)?)\s+(?P<name>\w+)\s*(?:=\s*(?P<value>[^;]+))?;'
        }
    }

    # 默认排除的目录和文件
    DEFAULT_EXCLUDES = {
        # 版本控制
        '.git', '.svn', '.hg',
        # Python
        '__pycache__', '*.pyc', '*.pyo', '*.pyd', '.Python', 'env/', 'venv/', '.env', '.venv',
        'pip-log.txt', 'pip-delete-this-directory.txt',
        # Node.js
        'node_modules/', 'npm-debug.log*', 'yarn-debug.log*', 'yarn-error.log*',
        # IDE
        '.idea/', '.vscode/', '*.swp', '*.swo',
        # 构建输出
        'build/', 'dist/', '*.egg-info/', '*.egg',
        # 其他
        '.DS_Store', 'Thumbs.db'
    }

    def __init__(self, project_path: str):
        """初始化 RulesGenerator"""
        print("\n🔄 初始化 RulesGenerator...")
        self.project_path = project_path
        self.analyzer = RulesAnalyzer(project_path)
        self.exclude_patterns = self._load_exclude_patterns()
        
        with tqdm.tqdm(total=4, desc="初始化进度") as pbar:
            # 编译正则表达式
            print("📝 编译模式...")
            self.compiled_patterns = self._compile_patterns()
            pbar.update(1)
            
            # 加载环境变量
            print("🔑 加载 API 配置...")
            load_dotenv()
            pbar.update(1)
            
            try:
                api_key = os.environ.get("GEMINI_API_KEY")
                api_base = os.environ.get("GOOGLE_API_BASE_URL", "https://generativelanguage.googleapis.com")
                
                if not api_key:
                    raise ValueError("未设置 GEMINI_API_KEY 环境变量")

                print("🤖 配置 Gemini AI...")
                print(f"📡 使用 API base URL: {api_base}")
                
                # 设置 30 秒超时
                signal.alarm(30)
                try:
                    genai.configure(
                        api_key=api_key,
                        transport="rest",
                        client_options={"api_endpoint": api_base}
                    )
                    pbar.update(1)
                    
                    print("🚀 启动聊天会话...")
                    self.model = genai.GenerativeModel(
                        model_name="gemini-2.0-flash-exp",
                        generation_config={
                            "temperature": 0.7,
                            "top_p": 0.95,
                            "top_k": 40,
                            "max_output_tokens": 8192,
                        }
                    )
                    self.chat_session = self.model.start_chat(history=[])
                    pbar.update(1)
                    
                    print("✅ RulesGenerator 初始化成功")
                    
                finally:
                    signal.alarm(0)
                    
            except TimeoutException:
                print("\n❌ API 初始化超时")
                raise
            except Exception as e:
                print(f"\n❌ 初始化 Gemini AI 时出错: {str(e)}")
                print("⚠️ 请确保在环境变量或 .env 文件中设置了 GEMINI_API_KEY 和 GOOGLE_API_BASE_URL")
                print("🔍 当前 API key:", api_key[:10] + "..." if api_key else "未找到")
                print("🌐 当前 base URL:", api_base)
                raise

    def _compile_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Precompile all regex patterns for better performance."""
        compiled = {}
        
        # Compile patterns for each category
        for category, patterns in self.PATTERNS.items():
            compiled[category] = {}
            
            if isinstance(patterns, dict):
                # Handle nested patterns (import, class, function)
                if category in ['import', 'class', 'function']:
                    for lang_group, pattern in patterns.items():
                        compiled[category][lang_group] = re.compile(pattern)
                # Handle common patterns
                elif category == 'common':
                    for pattern_name, pattern in patterns.items():
                        compiled[category][pattern_name] = re.compile(pattern)
                # Handle Unity patterns
                elif category == 'unity':
                    for pattern_name, pattern in patterns.items():
                        compiled[category][pattern_name] = re.compile(pattern)
            else:
                # Handle simple patterns
                compiled[category] = re.compile(patterns)
                
        return compiled

    def _get_timestamp(self) -> str:
        """Get current timestamp in standard format."""
        return datetime.now().strftime('%B %d, %Y at %I:%M %p')

    def _load_exclude_patterns(self) -> Set[str]:
        """从 .gitignore 和默认规则加载排除模式"""
        exclude_patterns = set(self.DEFAULT_EXCLUDES)
        
        # 读取 .gitignore 文件
        gitignore_path = os.path.join(self.project_path, '.gitignore')
        if os.path.exists(gitignore_path):
            try:
                with open(gitignore_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            exclude_patterns.add(line)
            except Exception as e:
                print(f"⚠️ 读取 .gitignore 文件时出错: {e}")
        
        return exclude_patterns

    def _should_exclude(self, path: str, is_dir: bool = False) -> bool:
        """检查是否应该排除某个路径"""
        # 转换为相对路径
        rel_path = os.path.relpath(path, self.project_path)
        
        # 创建 pathspec 对象来匹配 gitignore 样式的规则
        spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude_patterns)
        
        # 检查路径是否应该被排除
        return spec.match_file(rel_path)

    @with_progress("分析项目结构")
    def _analyze_project_structure(self) -> Dict[str, Any]:
        """分析项目结构并收集详细信息"""
        structure = {
            'files': [],
            'dependencies': {},
            'frameworks': [],
            'languages': {},
            'config_files': [],
            'code_contents': {},
            'directory_structure': {},
            'language_stats': {},
            'patterns': {
                'classes': [],
                'functions': [],
                'imports': [],
                'error_handling': [],
                'configurations': [],
                'naming_patterns': {},
                'code_organization': [],
                'variable_patterns': [],
                'function_patterns': [],
                'class_patterns': [],
                'error_patterns': [],
                'performance_patterns': [],
                'suggest_patterns': [],
                'directory_patterns': []
            }
        }

        # 跟踪目录统计
        dir_stats = {}
        
        # 获取所有非排除文件的总数
        total_files = 0
        for root, dirs, files in os.walk(self.project_path):
            # 过滤掉要排除的目录
            dirs[:] = [d for d in dirs if not self._should_exclude(os.path.join(root, d), True)]
            # 过滤掉要排除的文件
            files = [f for f in files if not self._should_exclude(os.path.join(root, f))]
            total_files += len(files)
        
        with tqdm.tqdm(total=total_files, desc="分析文件") as pbar:
            for root, dirs, files in os.walk(self.project_path):
                # 过滤掉要排除的目录
                dirs[:] = [d for d in dirs if not self._should_exclude(os.path.join(root, d), True)]
                
                rel_root = os.path.relpath(root, self.project_path)
                if rel_root == '.':
                    rel_root = ''
                    
                # 初始化目录统计
                dir_stats[rel_root] = {
                    'total_files': 0,
                    'code_files': 0,
                    'languages': {},
                    'frameworks': set(),
                    'patterns': {
                        'classes': 0,
                        'functions': 0,
                        'imports': 0
                    }
                }

                # 过滤掉要排除的文件
                files = [f for f in files if not self._should_exclude(os.path.join(root, f))]
                
                for file in files:
                    pbar.update(1)
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self.project_path)
                    
                    # Update directory statistics
                    dir_stats[rel_root]['total_files'] += 1
                    
                    # Analyze code files
                    file_ext = os.path.splitext(file)[1].lower()
                    if file_ext in ['.py', '.js', '.ts', '.tsx', '.kt', '.php', '.swift', '.cpp', '.c', '.h', '.hpp', '.cs', '.csx', '.java', '.rb', '.objc']:
                        structure['files'].append(rel_path)
                        dir_stats[rel_root]['code_files'] += 1
                        
                        # Update language statistics
                        lang = self._get_language_from_ext(file_ext)
                        dir_stats[rel_root]['languages'][lang] = dir_stats[rel_root]['languages'].get(lang, 0) + 1
                        structure['languages'][lang] = structure['languages'].get(lang, 0) + 1
                        
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                structure['code_contents'][rel_path] = content
                                
                                # Analyze based on file type
                                self._analyze_file(content, rel_path, structure, lang)
                                
                        except Exception as e:
                            print(f"⚠️ Error reading file {rel_path}: {e}")
                            continue

                    # Classify config files
                    elif file.endswith(('.json', '.ini', '.conf')):
                        structure['config_files'].append(rel_path)
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                                structure['patterns']['configurations'].append({
                                    'file': rel_path,
                                    'content': content
                                })
                        except Exception as e:
                            print(f"⚠️ Error reading config file {rel_path}: {e}")
                            continue

                # Add directory structure information
                if rel_root:
                    structure['directory_structure'][rel_root] = {
                        'stats': dir_stats[rel_root],
                        'parent': os.path.dirname(rel_root) or None
                    }

        # Analyze directory patterns
        self._analyze_directory_patterns(structure, dir_stats)
        
        return structure

    def _get_language_from_ext(self, ext: str) -> str:
        """Get programming language from file extension."""
        lang_map = {
            '.py': 'Python',
            '.js': 'JavaScript',
            '.ts': 'TypeScript',
            '.tsx': 'TypeScript/React',
            '.kt': 'Kotlin',
            '.php': 'PHP',
            '.swift': 'Swift',
            '.cpp': 'C++',
            '.c': 'C',
            '.h': 'C/C++ Header',
            '.hpp': 'C++ Header',
            '.cs': 'C#',
            '.csx': 'C# Script',
            '.java': 'Java',
            '.rb': 'Ruby',
            '.objc': 'Objective-C',
        }
        return lang_map.get(ext, 'Unknown')

    def _analyze_file(self, content: str, rel_path: str, structure: Dict[str, Any], language: str) -> None:
        """Generic file analyzer that handles all languages."""
        # Map language to pattern group
        pattern_groups = {
            'python': 'python',
            'javascript': 'web',
            'typescript': 'web',
            'csharp': 'system',
            'cpp': 'system',
            'c': 'system',
            'php': 'system',
            'kotlin': 'system',
            'swift': 'system',
            'java': 'web',
            'ruby': 'web',
            'objc': 'system',
        }
        pattern_group = pattern_groups.get(language, 'system')

        # Find patterns using named groups
        for pattern_type in ['import', 'class', 'function']:
            pattern = self.compiled_patterns[pattern_type][pattern_group]
            matches = pattern.finditer(content)
            
            for match in matches:
                try:
                    info = {}
                    # Get all named groups
                    groups = match.groupdict()
                    
                    # Handle imports
                    if pattern_type == 'import':
                        module = next((v for k, v in groups.items() if v and k.startswith('module')), None)
                        if module:
                            structure['dependencies'][module] = True
                            structure['patterns']['imports'].append(module)
                        continue
                        
                    # Handle classes and functions
                    name = next((v for k, v in groups.items() if v and (k == 'name' or k == 'n')), None)
                    if not name:
                        continue
                        
                    info['name'] = name
                    info['file'] = rel_path
                    info['type'] = pattern_type
                    
                    # Add parameters/base class if present
                    if 'params' in groups and groups['params']:
                        info['parameters'] = groups['params']
                    if 'base' in groups and groups['base']:
                        info['base'] = groups['base'].strip()
                    if 'return' in groups and groups['return']:
                        info['return_type'] = groups['return'].strip()
                        
                    # Add to appropriate pattern list
                    pattern_key = f'{pattern_type}_patterns'
                    structure['patterns'][pattern_key].append(info)
                    
                except Exception as e:
                    continue  # Skip on any error
                    
        # Handle web-specific patterns
        if language in ['typescript', 'javascript']:
            self._analyze_web_patterns(content, rel_path, structure)

        # Handle Unity-specific patterns for C#
        if language == 'csharp' and any(x in content for x in ['UnityEngine', 'MonoBehaviour', 'ScriptableObject']):
            self._analyze_unity_patterns(content, rel_path, structure)

    def _analyze_directory_patterns(self, structure: Dict[str, Any], dir_stats: Dict[str, Any]):
        """Analyze directory organization patterns."""
        for dir_path, stats in dir_stats.items():
            if not dir_path:  # Skip root directory
                continue
                
            # Analyze directory naming convention
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
                
            # Analyze directory purpose
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
                
            # Add directory pattern
            structure['patterns']['directory_patterns'].append({
                'path': dir_path,
                'name_pattern': pattern,
                'purpose': purpose,
                'languages': stats['languages'],
                'total_files': stats['total_files'],
                'code_files': stats['code_files'],
                'code_metrics': stats['patterns']
            })

    @retry_on_429(max_retries=3, delay=2)
    def _generate_ai_rules(self, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generate rules using Gemini AI based on project analysis."""
        try:
            # Analyze project
            project_structure = self._analyze_project_structure()
            
            # Create detailed prompt
            prompt = f"""As an AI assistant working in Cursor IDE, analyze this project to understand how you should behave and generate code that perfectly matches the project's patterns and standards.

Project Overview:
Language: {project_info.get('language', 'unknown')}
Framework: {project_info.get('framework', 'none')}
Type: {project_info.get('type', 'generic')}
Description: {project_info.get('description', 'Generic Project')}
Primary Purpose: Code generation and project analysis

Project Metrics:
- Files & Structure:
  - Total Files: {len(project_structure['files'])}
  - Config Files: {len(project_structure['config_files'])}
- Dependencies:
  - Frameworks: {', '.join(project_structure['frameworks']) or 'none'}
  - Core Dependencies: {', '.join(list(project_structure['dependencies'].keys())[:10])}
  - Total Dependencies: {len(project_structure['dependencies'])}

Project Ecosystem:
1. Development Environment:
- Project Structure:
{chr(10).join([f"- {f}" for f in project_structure['files'] if f.endswith(('.json', '.md', '.env', '.gitignore'))][:5])}
- IDE Configuration:
{chr(10).join([f"- {f}" for f in project_structure['files'] if '.vscode' in f or '.idea' in f][:5])}
- Build System:
{chr(10).join([f"- {f}" for f in project_structure['files'] if f in ['setup.py', 'requirements.txt', 'package.json', 'Makefile', 'composer.json', 'Gemfile', 'CMakeLists.txt', 'build.gradle', 'pom.xml', 'webpack.config.js']])}

2. Project Components:
- Core Modules:
{chr(10).join([f"- {f}: {sum(1 for p in project_structure['patterns']['function_patterns'] if p['file'] == f)} functions" for f in project_structure['files'] if f.endswith('.py, .js, .ts, .tsx, .kt, .php, .swift, .cpp, .c, .h, .hpp, .cs, .csx') and not any(x in f.lower() for x in ['setup', 'config'])][:5])}
- Support Modules:
{chr(10).join([f"- {f}" for f in project_structure['files'] if any(x in f.lower() for x in ['util', 'helper', 'common', 'shared'])][:5])}
- Templates:
{chr(10).join([f"- {f}" for f in project_structure['files'] if 'template' in f.lower()][:5])}

3. Module Organization Analysis:
- Core Module Functions:
{chr(10).join([f"- {f}: Primary module handling {f.split('_')[0].title()} functionality" for f in project_structure['files'] if f.endswith('.py, .js, .ts, .tsx, .kt, .php, .swift, .cpp, .c, .h, .hpp, .cs, .csx') and not any(x in f.lower() for x in ['setup', 'config'])][:5])}

- Module Dependencies:
{chr(10).join([f"- {f} depends on: {', '.join(list(set([imp.split('.')[0] for imp in project_structure['patterns']['imports'] if imp in f])))}" for f in project_structure['files'] if f.endswith('.py, .js, .ts, .tsx, .kt, .php, .swift, .cpp, .c, .h, .hpp, .cs, .csx')][:5])}

- Module Responsibilities:
Please analyze each module's code and describe its core responsibilities based on:
1. Function and class names
2. Import statements
3. Code patterns and structures
4. Documentation strings
5. Variable names and usage
6. Error handling patterns
7. Performance optimization techniques

- Module Organization Rules:
Based on the codebase analysis, identify and describe:
1. Module organization patterns
2. Dependency management approaches
3. Code structure conventions
4. Naming conventions
5. Documentation practices
6. Error handling strategies
7. Performance optimization patterns

Code Sample Analysis:
{chr(10).join(f"File: {file}:{chr(10)}{content[:10000]}..." for file, content in list(project_structure['code_contents'].items())[:50])}

Based on this detailed analysis, create behavior rules for AI to:
1. Replicate the project's exact code style and patterns
2. Match naming conventions precisely
3. Follow identical error handling patterns
4. Copy performance optimization techniques
5. Maintain documentation consistency
6. Keep current code organization
7. Preserve module boundaries
8. Use established logging methods
9. Follow configuration patterns

Return a JSON object defining AI behavior rules:
{{"ai_behavior": {{
    "code_generation": {{
        "style": {{
            "prefer": [],
            "avoid": []
        }},
        "error_handling": {{
            "prefer": [],
            "avoid": []
        }},
        "performance": {{
            "prefer": [],
            "avoid": []
        }},
        "suggest_patterns": {{
            "improve": [],
            "avoid": []
        }},
        "module_organization": {{
            "structure": [],  # Analyze and describe the current module structure
            "dependencies": [],  # Analyze actual dependencies between modules
            "responsibilities": {{}},  # Analyze and describe each module's core responsibilities
            "rules": [],  # Extract rules from actual code organization patterns
            "naming": {{}}  # Extract naming conventions from actual code
        }}
    }}
}}}}

Critical Guidelines for AI:
1. NEVER deviate from existing code patterns
2. ALWAYS match the project's exact style
3. MAINTAIN the current complexity level
4. COPY the existing skill level approach
5. PRESERVE all established practices
6. REPLICATE the project's exact style
7. UNDERSTAND pattern purposes"""
    
            # Get AI response
            response = self.chat_session.send_message(prompt)
            
            # Extract JSON
            json_match = re.search(r'({[\s\S]*})', response.text)
            if not json_match:
                print("⚠️ No JSON found in AI response")
                raise ValueError("Invalid AI response format")
                
            json_str = json_match.group(1)
            
            try:
                ai_rules = json.loads(json_str)
                
                if not isinstance(ai_rules, dict) or 'ai_behavior' not in ai_rules:
                    print("⚠️ Invalid JSON structure in AI response")
                    raise ValueError("Invalid AI rules structure")
                    
                return ai_rules
                
            except json.JSONDecodeError as e:
                print(f"⚠️ Error parsing AI response JSON: {e}")
                raise
                
        except Exception as e:
            print(f"⚠️ Error generating AI rules: {e}")
            raise

    @retry_on_429(max_retries=3, delay=2)
    def _generate_project_description(self, project_structure: Dict[str, Any]) -> str:
        """Generate project description using AI based on project analysis."""
        try:
            # Analyze core modules
            core_modules = []
            for file in project_structure.get('files', []):
                if file.endswith('.py') and not any(x in file.lower() for x in ['setup', 'config', 'test']):
                    module_info = {
                        'name': file,
                        'classes': [c for c in project_structure['patterns']['class_patterns'] if c['file'] == file],
                        'functions': [f for f in project_structure['patterns']['function_patterns'] if f['file'] == file],
                        'imports': [imp for imp in project_structure['patterns']['imports'] if imp in file]
                    }
                    core_modules.append(module_info)

            # Analyze main patterns
            main_patterns = {
                'error_handling': project_structure.get('patterns', {}).get('error_patterns', []),
                'performance': project_structure.get('patterns', {}).get('performance_patterns', []),
                'code_organization': project_structure.get('patterns', {}).get('code_organization', [])
            }

            # Create detailed prompt for AI
            prompt = f"""Analyze this project structure and create a detailed description (2-3 sentences) that captures its essence:

Project Overview:
1. Core Modules Analysis:
{chr(10).join([f"- {m['name']}: {len(m['classes'])} classes, {len(m['functions'])} functions" for m in core_modules])}

2. Module Responsibilities:
{chr(10).join([f"- {m['name']}: Main purpose indicated by {', '.join([c['name'] for c in m['classes'][:2]])}" for m in core_modules if m['classes']])}

3. Technical Implementation:
- Error Handling: {len(main_patterns['error_handling'])} patterns found
- Performance Optimizations: {len(main_patterns['performance'])} patterns found
- Code Organization: {len(main_patterns['code_organization'])} patterns found

4. Project Architecture:
- Total Files: {len(project_structure.get('files', []))}
- Core Python Modules: {len(core_modules)}
- External Dependencies: {len(project_structure.get('dependencies', {}))}

Based on this analysis, create a description that covers:
1. The project's main purpose and functionality
2. Key technical features and implementation approach
3. Target users and primary use cases
4. Unique characteristics or innovations

Format: Return a clear, concise description focusing on what makes this project unique.
Do not include technical metrics in the description."""

            # Get AI response
            response = self.chat_session.send_message(prompt)
            description = response.text.strip()
            
            # Validate description length and content
            if len(description.split()) > 100:  # Length limit
                description = ' '.join(description.split()[:100]) + '...'
            
            return description
            
        except Exception as e:
            print(f"⚠️ Error generating project description: {e}")
            return "A software project with automated analysis and rule generation capabilities."

    def _generate_markdown_rules(self, project_info: Dict[str, Any], ai_rules: Dict[str, Any]) -> str:
        """Generate rules in markdown format."""
        timestamp = self._get_timestamp()
        description = project_info.get('description', 'A software project with automated analysis and rule generation capabilities.')
        
        markdown = f"""# Project Rules

## Project Information
- **Version**: {project_info.get('version', '1.0')}
- **Last Updated**: {timestamp}
- **Name**: {project_info.get('name', 'Unknown')}
- **Language**: {project_info.get('language', 'unknown')}
- **Framework**: {project_info.get('framework', 'none')}
- **Type**: {project_info.get('type', 'application')}

## Project Description
{description}

## AI Behavior Rules

### Code Generation Style
#### Preferred Patterns
"""
        # Add preferred code generation patterns
        for pattern in ai_rules['ai_behavior']['code_generation']['style']['prefer']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n#### Patterns to Avoid\n"
        for pattern in ai_rules['ai_behavior']['code_generation']['style']['avoid']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n### Error Handling\n#### Preferred Patterns\n"
        for pattern in ai_rules['ai_behavior']['code_generation']['error_handling']['prefer']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n#### Patterns to Avoid\n"
        for pattern in ai_rules['ai_behavior']['code_generation']['error_handling']['avoid']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n### Performance\n#### Preferred Patterns\n"
        for pattern in ai_rules['ai_behavior']['code_generation']['performance']['prefer']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n#### Patterns to Avoid\n"
        for pattern in ai_rules['ai_behavior']['code_generation']['performance']['avoid']:
            markdown += f"- {pattern}\n"
            
        markdown += "\n### Module Organization\n#### Structure\n"
        for item in ai_rules['ai_behavior']['code_generation']['module_organization']['structure']:
            markdown += f"- {item}\n"
            
        markdown += "\n#### Dependencies\n"
        for dep in ai_rules['ai_behavior']['code_generation']['module_organization']['dependencies']:
            markdown += f"- {dep}\n"
            
        markdown += "\n#### Module Responsibilities\n"
        for module, resp in ai_rules['ai_behavior']['code_generation']['module_organization']['responsibilities'].items():
            markdown += f"- **{module}**: {resp}\n"
            
        markdown += "\n#### Rules\n"
        for rule in ai_rules['ai_behavior']['code_generation']['module_organization']['rules']:
            markdown += f"- {rule}\n"
            
        markdown += "\n#### Naming Conventions\n"
        for category, convention in ai_rules['ai_behavior']['code_generation']['module_organization']['naming'].items():
            markdown += f"- **{category}**: {convention}\n"
            
        return markdown

    @with_progress("生成规则文件")
    def generate_rules_file(self, project_info: Dict[str, Any] = None, format: str = 'json') -> str:
        """生成 .cursorrules 文件"""
        try:
            with tqdm.tqdm(total=5, desc="生成进度") as pbar:
                # 使用分析器如果没有提供 project_info
                if project_info is None:
                    print("📊 分析项目信息...")
                    project_info = self.analyzer.analyze_project_for_rules()
                pbar.update(1)
                
                # 分析项目结构
                print("🔍 分析项目结构...")
                project_structure = self._analyze_project_structure()
                pbar.update(1)
                
                # 生成 AI 规则
                print("🤖 生成 AI 规则...")
                ai_rules = self._generate_ai_rules(project_info)
                pbar.update(1)
                
                # 生成项目描述
                print("📝 生成项目描述...")
                description = self._generate_project_description(project_structure)
                project_info['description'] = description
                pbar.update(1)
                
                # 创建规则文件
                print("💾 保存规则文件...")
                rules_file = os.path.join(self.project_path, '.cursorrules')
                
                if format.lower() == 'markdown':
                    content = self._generate_markdown_rules(project_info, ai_rules)
                    with open(rules_file, 'w', encoding='utf-8') as f:
                        f.write(content)
                else:  # JSON format
                    rules = {
                        "version": "1.0",
                        "last_updated": self._get_timestamp(),
                        "project": {
                            **project_info,
                            "description": description
                        },
                        "ai_behavior": ai_rules['ai_behavior']
                    }
                    with open(rules_file, 'w', encoding='utf-8') as f:
                        json.dump(rules, f, indent=2, ensure_ascii=False)
                pbar.update(1)
                
                print(f"\n✅ 规则文件已生成: {rules_file}")
                return rules_file
                
        except Exception as e:
            print(f"\n❌ 生成规则失败: {str(e)}")
            raise

    def _analyze_web_patterns(self, content: str, rel_path: str, structure: Dict[str, Any]) -> None:
        """Analyze React/Next.js specific patterns."""
        # Find interfaces and types
        for match in self.compiled_patterns['common']['interface'].finditer(content):
            structure['patterns']['class_patterns'].append({
                'name': match.group(1),
                'type': 'interface/type',
                'inheritance': match.group(2).strip() if match.group(2) else '',
                'file': rel_path
            })

        # Find React components
        for match in self.compiled_patterns['common']['jsx_component'].finditer(content):
            component_name = match.group(1)
            if component_name[0].isupper():  # React components start with uppercase
                structure['patterns']['class_patterns'].append({
                    'name': component_name,
                    'type': 'react_component',
                    'file': rel_path
                })

        # Find React hooks
        for hook in re.finditer(self.compiled_patterns['common']['react_hook'], content):
            structure['patterns']['function_patterns'].append({
                'name': hook.group(0),
                'type': 'react_hook',
                'file': rel_path
            })

        # Find Next.js specific patterns
        if any(x in rel_path for x in ['pages/', 'app/']):
            # Check for Next.js data fetching methods
            for method in re.finditer(self.compiled_patterns['common']['next_api'], content):
                structure['patterns']['function_patterns'].append({
                    'name': method.group(0),
                    'type': 'next_data_fetching',
                    'file': rel_path
                })

            # Analyze page/route structure
            page_match = re.search(self.compiled_patterns['common']['next_page'], rel_path)
            if page_match:
                structure['patterns']['code_organization'].append({
                    'type': 'next_page',
                    'route': page_match.group('route'),
                    'nested': page_match.group('nested'),
                    'file': rel_path
                })

            # Check for layouts
            if re.search(self.compiled_patterns['common']['next_layout'], rel_path):
                structure['patterns']['code_organization'].append({
                    'type': 'next_layout',
                    'file': rel_path
                })

        # Find styled-components patterns
        for match in re.finditer(self.compiled_patterns['common']['styled_component'], content):
            structure['patterns']['code_organization'].append({
                'type': 'styled_component',
                'element': match.group('element') if match.group('element') else 'css',
                'file': rel_path
            })

    def _analyze_unity_patterns(self, content: str, rel_path: str, structure: Dict[str, Any]) -> None:
        """Analyze Unity-specific patterns in C# scripts."""
        # Find MonoBehaviour and ScriptableObject components
        for match in self.compiled_patterns['unity']['component'].finditer(content):
            structure['patterns']['class_patterns'].append({
                'name': match.group(0),
                'type': 'unity_component',
                'file': rel_path
            })

        # Find Unity lifecycle methods
        for match in self.compiled_patterns['unity']['lifecycle'].finditer(content):
            structure['patterns']['function_patterns'].append({
                'name': match.group(0),
                'type': 'unity_lifecycle',
                'file': rel_path
            })

        # Find Unity attributes
        for match in self.compiled_patterns['unity']['attribute'].finditer(content):
            structure['patterns']['code_organization'].append({
                'type': 'unity_attribute',
                'name': match.group(0),
                'parameters': match.group('params') if match.group('params') else '',
                'file': rel_path
            })

        # Find Unity types
        for match in self.compiled_patterns['unity']['type'].finditer(content):
            structure['patterns']['class_patterns'].append({
                'name': match.group(0),
                'type': 'unity_type',
                'file': rel_path
            })

        # Find Unity events
        for match in self.compiled_patterns['unity']['event'].finditer(content):
            structure['patterns']['code_organization'].append({
                'type': 'unity_event',
                'event_type': match.group('type'),
                'name': match.group('name'),
                'file': rel_path
            })

        # Find Unity serialized fields
        for match in self.compiled_patterns['unity']['field'].finditer(content):
            structure['patterns']['code_organization'].append({
                'type': 'unity_field',
                'field_type': match.group(1),
                'name': match.group(2),
                'file': rel_path
            })

