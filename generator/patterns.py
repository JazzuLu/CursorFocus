"""
模式匹配正则表达式集合
此模块包含用于代码分析的各种正则表达式模式
"""

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