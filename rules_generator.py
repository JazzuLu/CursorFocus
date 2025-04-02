import os
import json
from typing import Dict, Any, List, Set
from datetime import datetime
import google.generativeai as genai
import re
from rules_analyzer import RulesAnalyzer
from dotenv import load_dotenv
from patterns_analyzer import PatternsAnalyzer

class RulesGenerator:
    def __init__(self, project_path: str):
        """初始化 RulesGenerator"""
        print("\n🔄 初始化 RulesGenerator...")
        self.project_path = project_path
        self.analyzer = RulesAnalyzer(project_path)
        self.exclude_patterns = self._load_exclude_patterns()
        
        # Initialize pattern analyzer
        patterns_analyzer = PatternsAnalyzer()
        self.compiled_patterns = patterns_analyzer.compiled_patterns
        self.get_language_from_ext = patterns_analyzer.get_language_from_ext
        
        # Load environment variables from .env
        load_dotenv()
        
        # Initialize Gemini AI
        try:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is required")

            genai.configure(api_key=api_key)
            
            # Get model name from environment or use default
            model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro-exp-03-25")
            
            self.model = genai.GenerativeModel(
                model_name=model_name,
            )
            self.chat_session = self.model.start_chat(history=[])
            
        except Exception as e:
            print(f"\n⚠️ Error when initializing Gemini AI: {e}")
            raise

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
            # Skip ignored directories
            dirs[:] = [d for d in dirs if not any(x in d for x in ['node_modules', 'venv', '.git', '__pycache__', 'build', 'dist'])]
            
            rel_root = os.path.relpath(root, self.project_path)
            if rel_root == '.':
                rel_root = ''
                
            # Initialize directory statistics
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

            for file in files:
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
                    lang = self.get_language_from_ext(file_ext)
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

    def _generate_ai_rules(self, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """Generate rules using Gemini AI based on project analysis."""
        try:
            # Analyze project
            project_structure = self._analyze_project_structure()
            
            # 使用导入的 prompt 模板
            prompt = get_ai_rules_prompt(project_info, project_structure)
    
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
