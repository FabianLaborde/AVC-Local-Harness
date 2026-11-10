"""Typed, local form vocabulary. Models select data; they never supply XAML/C#."""
import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

KINDS = ['card', 'column', 'row', 'title', 'text', 'avatar', 'field', 'password',
         'checkbox', 'link', 'button', 'separator']
MAX_NODES = 60
ICONS = {
    'user': 'M12,3 A4,4 0 1 1 11.99,3 M4,21 C4,12 20,12 20,21 Z',
    'lock': 'M6,10 L18,10 L18,22 L6,22 Z M8,10 L8,6 A4,4 0 0 1 16,6 L16,10',
    'mail': 'M2,5 L22,5 L22,19 L2,19 Z M2,5 L12,13 L22,5',
    'search': 'M10,3 A7,7 0 1 1 9.99,3 M15,15 L22,22',
    'check': 'M3,12 L9,18 L21,5',
}
SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'supported': {'type': 'boolean'}, 'limitations': {'type': 'array', 'items': {'type': 'string'}},
    'title': {'type': 'string'}, 'width': {'type': 'integer'}, 'height': {'type': 'integer'},
    'background': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': {'type': 'string'}},
    'nodes': {'type': 'array', 'minItems': 1, 'maxItems': MAX_NODES, 'items': {
        'type': 'object', 'additionalProperties': False, 'properties': {
            'id': {'type': 'string'}, 'parent': {'type': 'string'},
            'kind': {'type': 'string', 'enum': KINDS}, 'text': {'type': 'string'},
            'icon': {'type': 'string', 'enum': ['none'] + list(ICONS)},
            'background': {'type': 'string'}, 'foreground': {'type': 'string'},
            'size': {'type': 'integer'}, 'required': {'type': 'boolean'},
            'action': {'type': 'string', 'enum': ['none', 'validate_required']},
            'weight': {'type': 'integer', 'minimum': 1, 'maximum': 12},
            'value': {'type': 'string'}
        }, 'required': ['id', 'parent', 'kind', 'text']}}
}, 'required': ['supported', 'limitations', 'title', 'width', 'height', 'background', 'nodes']}
SCHEMA['properties']['window_fit']={'enum':['fixed','content_height']}
SCHEMA['properties']['outer_margin']={'type':'integer','minimum':0,'maximum':96}
SCHEMA['properties']['density']={'enum':['comfortable','compact','tight']}
SCHEMA['properties']['viewport_fit']={'enum':['scroll','fit']}
SCHEMA['properties']['independent_scroll']={
    'type':'object','additionalProperties':False,
    'properties':{
        'container':{'type':'string','pattern':'^n[0-9]{1,2}$'},
        'fixed_children':{'type':'array','minItems':1,'maxItems':8,'uniqueItems':True,
                          'items':{'type':'string','pattern':'^n[0-9]{1,2}$'}},
    },
    'required':['container','fixed_children'],
}
# Constrain each component's capabilities in the decoding schema itself.
# A column cannot receive a button action; fields explicitly declare icon/required.
variants=[]
for kind in KINDS:
    props={k:dict(v) for k,v in SCHEMA['properties']['nodes']['items']['properties'].items()
           if k in ('id','parent','kind','text','background','foreground','size','weight')}
    props['id']={'type':'string','pattern':'^n[0-9]{1,2}$'}
    props['parent']={'type':'string','pattern':'^(root|n[0-9]{1,2})$'}
    props['kind']={'type':'string','const':kind}
    if kind in ('card','column','row'):props['text']={'type':'string','const':''}
    for key in ('background','foreground'):props[key]={'type':'string','pattern':'^#[0-9A-Fa-f]{6}$'}
    props['size']={'type':'integer','minimum':40 if kind=='avatar' else 10,'maximum':120 if kind=='avatar' else 36}
    if kind in ('avatar','field','password'):props['icon']={'type':'string','enum':['none']+list(ICONS)}
    if kind in ('field','password'):props['required']={'type':'boolean'}
    if kind in ('field','password'):props['value']={'type':'string','maxLength':160}
    if kind=='button':props['action']={'type':'string','enum':['none','validate_required']}
    if kind in ('title','text','link'):props['align']={'enum':['Left','Center','Right','Stretch']}
    variants.append({'type':'object','properties':props,'required':list(props),'additionalProperties':False})
SCHEMA['properties']['nodes']['items']={'oneOf':variants}
PROMPT = '''Convertí el pedido en una interfaz WPF usando SOLO el catálogo JSON.
No generes código. root es un padre implícito, NO lo emitas como nodo. Los ids son n1,
n2, etc. El primer nodo puede ser n1 con parent=root. Emití padres antes que hijos.
Contenedores: card (tarjeta redondeada), column (vertical), row (elementos lado a lado).
Componentes: title, text, avatar (círculo con icono), field, password, checkbox, link,
button, separator. field/password contienen su icono y su etiqueta dentro del borde.
El text de card/column/row SIEMPRE es "": un contenedor no dibuja su texto. Cada título,
leyenda, opción de menú, campo, estado y botón visible debe ser un nodo hijo explícito.
No resumas una lista de controles en el text de un contenedor. Ningún contenedor puede
quedar vacío. value es el valor visual inicial de field/password; usá "" si no se indicó.
Iconos: none, user, lock, mail, search, check. Colores SOLO #RRGGBB o #AARRGGBB.
background exterior son dos colores de un degradado vertical. size es tamaño de letra
(10..36); en avatar es diámetro (40..120). Usá filas para elementos lado a lado.
weight es un entero de 1 a 12. Dentro de row controla proporciones horizontales y
dentro de column controla proporciones verticales cuando los hermanos tienen pesos
distintos. Por ejemplo, cabecera weight=1 y cuerpo weight=11; menú weight=1 y contenido
weight=4. AVC calcula tamaños y posiciones; no describas ni inventes coordenadas.
El único nodo con parent=root SIEMPRE lleva weight=1 porque allí no tiene efecto.
Para una cabecera arriba y menú/contenido debajo: column raíz -> row cabecera + row cuerpo;
dentro del row cuerpo
ubicá column menú weight=1 y column contenido weight=4. No pongas menú y contenido como
hijos directos consecutivos de una column porque quedarían uno debajo del otro.
column y row pueden llevar background para cabeceras, menús y franjas de color.
Acción permitida: validate_required, únicamente en button; valida los campos required.
No autentica ni conecta servicios. link es una leyenda sin navegación. Si el pedido
requiere funciones no soportadas, supported=false y explicá en limitations. No inventes
una función equivalente. No agregues contenidos no pedidos. Máximo 60 nodos; compacto.
align permite Left, Center, Right o Stretch en textos y títulos.
window_fit=content_height ajusta la altura al contenido; fixed conserva altura explícita.
outer_margin es margen exterior uniforme 0..96 (predeterminado 24). density controla
el espaciado global: comfortable, compact o tight. viewport_fit=fit reduce el conjunto
proporcionalmente para evitar desplazamiento; scroll conserva desplazamiento automático.
independent_scroll identifica una column cuyo contenido central se desplaza y enumera en
fixed_children sus hijos directos que deben permanecer visibles fuera del desplazamiento.
Conservá window_fit, outer_margin, density y viewport_fit si no se pide cambiarlos.
Para cambios de un plan existente, conservá todo lo que el pedido no modifica.
Usá required=true en campos que el pedido exige completar. Declarar icon=user o
icon=lock es lo que permite dibujar esos iconos; no basta mencionarlos en text.
Podés crear formularios, paneles, tableros y pantallas de escritorio completas. Primero
representá la jerarquía visual y el orden; usá pesos discretos para las proporciones.
'''


def validate(plan):
    if not isinstance(plan, dict) or plan.get('supported') is not True:
        raise ValueError('Pedido fuera del catálogo: ' + str(plan.get('limitations', []) if isinstance(plan, dict) else plan))
    if plan.get('limitations'):
        raise ValueError('El plan declara pendientes: ' + str(plan['limitations']))
    def color(value):
        if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
            raise ValueError('Color inválido: ' + str(value))
    for key, low, high in [('width', 360, 1600), ('height', 420, 1200)]:
        if type(plan.get(key)) is not int or not low <= plan[key] <= high:
            raise ValueError('Dimensión inválida: ' + key)
    if len(plan.get('background', [])) != 2: raise ValueError('Se requieren dos colores de fondo')
    for c in plan['background']: color(c)
    if not isinstance(plan.get('title'), str) or len(plan['title']) > 160: raise ValueError('Título inválido')
    if plan.get('window_fit','fixed') not in ('fixed','content_height'):raise ValueError('Ajuste de ventana inválido')
    if plan.get('density','compact') not in ('comfortable','compact','tight'):raise ValueError('Densidad inválida')
    if plan.get('viewport_fit','scroll') not in ('scroll','fit'):raise ValueError('Ajuste al área visible inválido')
    margin=plan.get('outer_margin',24)
    if type(margin) is not int or not 0<=margin<=96:raise ValueError('Margen exterior inválido')
    nodes=plan.get('nodes', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES: raise ValueError('Cantidad de componentes inválida')
    seen={'root': None}; depth={'root': 0}
    for n in nodes:
        if not isinstance(n, dict): raise ValueError('Componente inválido')
        ident=n.get('id', '')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,39}', ident) or ident in seen: raise ValueError('Id inválido o duplicado')
        parent=n.get('parent'); kind=n.get('kind')
        if parent not in seen or (parent != 'root' and seen[parent]['kind'] not in ('card','column','row')):
            raise ValueError('Padre inválido: ' + str(parent))
        if kind not in KINDS or not isinstance(n.get('text'), str) or len(n['text']) > 300: raise ValueError('Contenido inválido')
        depth[ident]=depth[parent]+1
        if depth[ident]>8: raise ValueError('Jerarquía demasiado profunda')
        for key in ('background','foreground'):
            if key in n: color(n[key])
        if n.get('icon','none') not in ['none']+list(ICONS): raise ValueError('Icono desconocido')
        size=n.get('size', 80 if kind=='avatar' else 15)
        low,high=(40,120) if kind=='avatar' else (10,36)
        if type(size) is not int or not low<=size<=high: raise ValueError('Tamaño inválido')
        if type(n.get('required',False)) is not bool: raise ValueError('required inválido')
        if not isinstance(n.get('value',''),str) or len(n.get('value',''))>160:raise ValueError('Valor inicial inválido')
        if n.get('required') and kind not in ('field','password'): raise ValueError('required solo corresponde a campos')
        if n.get('action','none') not in ('none','validate_required'): raise ValueError('Acción desconocida')
        if n.get('action','none')!='none' and kind!='button': raise ValueError('Acción fuera de botón')
        if n.get('align','Left') not in ('Left','Center','Right','Stretch'): raise ValueError('Alineación inválida')
        weight=n.get('weight',1)
        if type(weight) is not int or not 1<=weight<=12: raise ValueError('Proporción inválida')
        if parent!='root' and seen[parent]['kind'] not in ('row','column') and weight!=1:
            raise ValueError('weight solo cambia proporciones dentro de row o column')
        if parent=='root' and weight!=1:raise ValueError('weight de raíz debe ser 1')
        seen[ident]=n
    child_count={n['id']:0 for n in nodes}
    for n in nodes:
        if n['parent']!='root':child_count[n['parent']]+=1
    for n in nodes:
        is_container=n['kind'] in ('card','column','row')
        if is_container and n['text']!='':raise ValueError('Los contenedores no muestran texto')
        if is_container and not child_count[n['id']]:raise ValueError('Contenedor vacío: '+n['id'])
        if not is_container and child_count[n['id']]:raise ValueError('Un componente hoja no puede contener hijos')
    independent=plan.get('independent_scroll')
    if independent is not None:
        if not isinstance(independent,dict) or set(independent)!={'container','fixed_children'}:
            raise ValueError('Región de desplazamiento independiente inválida')
        container=independent.get('container');fixed=independent.get('fixed_children')
        by_id={n['id']:n for n in nodes}
        if container not in by_id or by_id[container]['kind']!='column':
            raise ValueError('La región desplazable debe ser una column existente')
        direct=[n['id'] for n in nodes if n['parent']==container]
        if (not isinstance(fixed,list) or not fixed or len(fixed)!=len(set(fixed))
                or any(i not in direct for i in fixed) or len(fixed)>=len(direct)):
            raise ValueError('Los elementos fijos deben ser hijos directos de la región y no pueden ocuparla completa')
        positions=[direct.index(i) for i in fixed]
        if positions!=list(range(min(positions),len(direct))):
            raise ValueError('Los elementos fijos deben formar el tramo final del contenedor')
    return plan


def normalize(plan, visual_only=False):
    """Remove harmless model noise and enforce conversational scope before validation."""
    if not isinstance(plan, dict):
        return plan
    result=copy.deepcopy(plan)
    for node in result.get('nodes', []):
        # A root weight has no geometric meaning. Rejecting a complete screen for it
        # made the strict contract less reliable without protecting any invariant.
        if isinstance(node,dict) and node.get('parent')=='root':
            node['weight']=1
        if visual_only and isinstance(node,dict) and node.get('kind')=='button':
            node['action']='none'
    return result


def render(plan):
    validate(plan)
    root_plan_nodes=[n for n in plan['nodes'] if n['parent']=='root']
    screen_mode=len(root_plan_nodes)==1 and root_plan_nodes[0]['kind']=='column'
    density=plan.get('density','compact' if screen_mode else 'comfortable')
    spacing={
        'comfortable':(24,8,12,8,6,10,7,26,12,8),
        'compact':(12,4,8,4,3,6,3,20,12,8),
        'tight':(6,2,4,2,1,3,1,16,6,2),
    }[density]
    (card_padding,card_margin,container_padding,row_padding,leaf_margin,
     field_padding,field_margin,field_height,button_padding,button_margin)=spacing
    # Treat all model strings as text, never markup extensions or code.
    def attr(s): return quoteattr(('{}' if str(s).startswith('{') else '')+str(s))
    def icon(name, color, size=22):
        if name=='none': return ''
        return f'<Viewbox Width="{size}" Height="{size}"><Canvas Width="24" Height="24"><Path Data="{ICONS[name]}" Stroke={attr(color)} StrokeThickness="1.6" StrokeLineJoin="Round"/></Canvas></Viewbox>'
    children={n['id']:[] for n in plan['nodes']};children['root']=[]
    for n in plan['nodes']: children[n['parent']].append(n)
    scroll_path=set()
    if plan.get('independent_scroll'):
        by_id={n['id']:n for n in plan['nodes']}
        current=plan['independent_scroll']['container']
        while current!='root':
            scroll_path.add(current);current=by_id[current]['parent']
    fields=[];handlers=[]
    def node(n):
        kind=n['kind'];ident=n['id'];text=attr(n['text'])
        fg=n.get('foreground','#FFFFFF' if kind in ('avatar','button') else '#243247');bg=n.get('background','#FFFFFF')
        size=n.get('size',80 if kind=='avatar' else 24 if kind=='title' else 15)
        content=''.join(node(c) for c in children[ident])
        if kind in ('card','column'):
            direct=children[ident]
            weights=[c.get('weight',1) for c in direct]
            independent=plan.get('independent_scroll') if kind=='column' else None
            if independent and independent.get('container')==ident:
                parts=list(ET.fromstring('<Root>'+content+'</Root>'))
                fixed=set(independent['fixed_children'])
                scrolling=''.join(ET.tostring(part,encoding='unicode')
                                  for child,part in zip(direct,parts) if child['id'] not in fixed)
                persistent=''.join(ET.tostring(part,encoding='unicode')
                                   for child,part in zip(direct,parts) if child['id'] in fixed)
                content=('<Grid><Grid.RowDefinitions><RowDefinition Height="*"/>'
                         '<RowDefinition Height="Auto"/></Grid.RowDefinitions>'
                         '<ScrollViewer Grid.Row="0" VerticalScrollBarVisibility="Auto" '
                         'HorizontalScrollBarVisibility="Disabled"><StackPanel>'+scrolling+
                         '</StackPanel></ScrollViewer><StackPanel Grid.Row="1">'+persistent+
                         '</StackPanel></Grid>')
            weighted_column=(not (independent and independent.get('container')==ident)
                             and kind=='column' and len(direct)>1 and len(set(weights))>1
                             and plan.get('window_fit','fixed')!='content_height')
            if weighted_column:
                parts=list(ET.fromstring('<Root>'+content+'</Root>'))
                rows=''.join(
                    f'<RowDefinition Height="{"*" if c["id"] in scroll_path else "Auto"}"/>'
                    if scroll_path and any(child['id'] in scroll_path for child in direct)
                    else f'<RowDefinition Height="{c.get("weight",1)}*"/>'
                    for c in direct)
                root_extent=((' Height' if plan.get('independent_scroll') else ' MinHeight')+
                             f'="{max(100,plan["height"]-2*plan.get("outer_margin",24)-40)}"')
                content='<Grid'+(root_extent if n['parent']=='root' else '')+'><Grid.RowDefinitions>'+rows+'</Grid.RowDefinitions>'+''.join(f'<Grid Grid.Row="{i}">'+ET.tostring(c,encoding='unicode')+'</Grid>' for i,c in enumerate(parts))+'</Grid>'
            elif not (independent and independent.get('container')==ident):
                content='<StackPanel>'+content+'</StackPanel>'
            if kind=='card':content=f'<Border Background={attr(bg)} CornerRadius="16" Padding="{card_padding}" Margin="0,{card_margin}">{content}</Border>'
            elif 'background' in n:content=f'<Border Background={attr(bg)} Padding="{container_padding}">{content}</Border>'
            return content
        if kind=='row':
            cols=''.join(f'<ColumnDefinition Width="{c.get("weight",1)}*"/>' for c in children[ident])
            # content already rendered once, including handler bookkeeping.
            parts=list(ET.fromstring('<Root>'+content+'</Root>'))
            grid='<Grid Margin="0,4"><Grid.ColumnDefinitions>'+cols+'</Grid.ColumnDefinitions>'+''.join(f'<Grid Grid.Column="{i}" Margin="4,0">'+ET.tostring(c,encoding='unicode')+'</Grid>' for i,c in enumerate(parts))+'</Grid>'
            return f'<Border Background={attr(bg)} Padding="{row_padding}">{grid}</Border>' if 'background' in n else grid
        common=f'Foreground={attr(fg)} FontSize="{size}"'
        if kind in ('title','text','link'):
            painted=(f' Background={attr(bg)} Padding="4"' if 'background' in n else '')
            return f'<TextBlock Text={text} {common}{painted} TextWrapping="Wrap" Margin="0,{leaf_margin}"'+(' FontWeight="SemiBold"' if kind=='title' else '')+f' HorizontalAlignment="{n.get("align", "Center" if kind=="title" else "Stretch")}"'+(' TextDecorations="Underline"' if kind=='link' else '')+'/>'
        if kind=='avatar':
            return f'<Border Width="{size}" Height="{size}" CornerRadius="{size/2}" BorderThickness="1" BorderBrush={attr(fg)} Background={attr(n.get("background","#2879B8"))} HorizontalAlignment="Center" Margin="0,8">'+icon(n.get('icon','user'),n.get('foreground','#FFFFFF'),round(size*.55))+'</Border>'
        if kind in ('field','password'):
            if n.get('required'):fields.append((ident,kind))
            control='TextBox' if kind=='field' else 'PasswordBox'
            initial=(' Text='+attr(n.get('value','')) if kind=='field' else ' Password='+attr(n.get('value','')))
            return f'<Border Background={attr(bg)} BorderBrush="#CBD5E1" BorderThickness="1" CornerRadius="7" Padding="{field_padding}" Margin="0,{field_margin}"><Grid><Grid.ColumnDefinitions><ColumnDefinition Width="Auto"/><ColumnDefinition Width="*"/></Grid.ColumnDefinitions><Grid Margin="0,0,10,0">'+icon(n.get('icon','none'),fg)+f'</Grid><StackPanel Grid.Column="1"><TextBlock Text={text} {common}/><{control} Name="C_{ident}" AutomationProperties.Name={text}{initial} {common} Background={attr(bg)} BorderThickness="0" MinHeight="{field_height}" Padding="2"/></StackPanel></Grid></Border>'
        if kind=='checkbox':return f'<CheckBox Content={text} {common} Margin="0,8" VerticalAlignment="Center"/>'
        if kind=='separator':return '<Border Height="1" Background="#CBD5E1" Margin="0,10"/>'
        if kind=='button':
            action=n.get('action','none')
            if action=='validate_required':handlers.append(ident)
            return f'<Button Content={text} {common} Background={attr(n.get("background","#2563EB"))} Padding="{button_padding}" Margin="0,{button_margin}" HorizontalAlignment="Stretch"'+(f' Click="On_{ident}"' if action=='validate_required' else '')+'/>'
        raise ValueError(kind)
    body=''.join(node(n) for n in children['root'])
    sizing='SizeToContent="Height" MinHeight="0"' if plan.get('window_fit')=='content_height' else f'Height="{plan["height"]}" MinHeight="420"'
    root_nodes=children['root']
    vertical='Top' if len(root_nodes)==1 and root_nodes[0]['kind']=='column' else 'Center'
    if plan.get('viewport_fit','scroll')=='fit':
        usable=max(300,plan['width']-2*plan.get('outer_margin',24)-20)
        host=f'<Grid Margin="{plan.get("outer_margin",24)}"><Viewbox Stretch="Uniform" StretchDirection="DownOnly" HorizontalAlignment="Stretch" VerticalAlignment="Stretch"><Grid Width="{usable}"><StackPanel VerticalAlignment="{vertical}">{body}</StackPanel></Grid></Viewbox></Grid>'
    elif plan.get('window_fit')=='content_height':
        host=f'<Grid Margin="{plan.get("outer_margin",24)}"><StackPanel VerticalAlignment="{vertical}">{body}</StackPanel></Grid>'
    elif plan.get('independent_scroll'):
        host=f'<Grid Margin="{plan.get("outer_margin",24)}">{body}</Grid>'
    else:
        host=f'<ScrollViewer VerticalScrollBarVisibility="Auto" HorizontalScrollBarVisibility="Disabled"><Grid Margin="{plan.get("outer_margin",24)}"><StackPanel VerticalAlignment="{vertical}">{body}</StackPanel></Grid></ScrollViewer>'
    xaml=f'''<Window x:Class="Aplicacion.MainWindow" xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Title={attr(plan['title'])} Width="{plan['width']}" {sizing} MinWidth="360">
<Window.Background><LinearGradientBrush StartPoint="0,0" EndPoint="0,1"><GradientStop Color="{plan['background'][0]}" Offset="0"/><GradientStop Color="{plan['background'][1]}" Offset="1"/></LinearGradientBrush></Window.Background>
{host}</Window>'''
    checks=' || '.join(f'string.IsNullOrWhiteSpace(C_{i}.{ "Password" if k=="password" else "Text"})' for i,k in fields) or 'false'
    methods='\n'.join(f'''private void On_{i}(object sender, RoutedEventArgs e) {{ MessageBox.Show(!CatalogFieldsComplete() ? "Completá los campos obligatorios." : "Campos completos. Validación de demostración; no se autenticó una cuenta.", "Validación"); }}''' for i in handlers)
    cs='using System.Windows;\nnamespace Aplicacion;\npublic partial class MainWindow : Window { public MainWindow() { InitializeComponent(); }\n'+f'internal bool CatalogFieldsComplete() => !({checks});\n'+methods+'\n}'
    ET.fromstring(xaml)
    return {'MainWindow.xaml':xaml,'MainWindow.xaml.cs':cs}


def digest(raw): return hashlib.sha256(raw).hexdigest()


def creation_request(instruction):
    """Recognize broad natural-language requests for a new typed WPF interface."""
    if not isinstance(instruction,str):return False
    create=re.search(r'(?i)\b(cre[áa](?:r|me)?|gener[áa](?:r|me)?|arm[áa](?:r|me)?|diseñ[áa](?:r|me)?|constru(?:ir|ye|[íi])|hacer|quiero)\b',instruction)
    ui=re.search(r'(?i)\b(interfa[cz]|pantalla|ventana|formulario|login|inicio de sesi[oó]n|registro|panel|tablero|dashboard|men[uú]|aplicaci[oó]n(?: de escritorio)?)\b',instruction)
    return bool(create and ui)


def eligible(project):
    """Only new folders or unchanged catalog-owned source sets are eligible."""
    marker=project/'.avc_catalog.json'
    if marker.is_file(): return True  # ownership hashes checked before any overwrite
    return not any(p.suffix.lower() in ('.csproj','.xaml','.cs','.sln') for p in project.rglob('*') if p.is_file())


def creation_eligible(project):
    """Allow an empty folder or the untouched WPF scaffold left by a failed AVC creation."""
    if eligible(project):return True
    ignored={'bin','obj','avc_publish'}
    sources=[p for p in project.rglob('*') if p.is_file() and not set(p.relative_to(project).parts)&ignored
             and p.suffix.lower() in ('.csproj','.xaml','.cs','.sln')]
    allowed={'Aplicacion.csproj','App.xaml','App.xaml.cs','AssemblyInfo.cs','MainWindow.xaml','MainWindow.xaml.cs'}
    if not sources or any(str(p.relative_to(project)).replace('\\','/') not in allowed for p in sources):return False
    xaml=project/'MainWindow.xaml';code=project/'MainWindow.xaml.cs';projects=list(project.glob('*.csproj'))
    if len(projects)!=1 or not xaml.is_file() or not code.is_file():return False
    try:
        markup=xaml.read_text(encoding='utf-8-sig');behind=code.read_text(encoding='utf-8-sig')
    except (OSError,UnicodeError):return False
    return (len(markup)<3000 and len(behind)<2000 and
            re.search(r'<Grid>\s*</Grid>',markup,re.S) is not None and
            'Title="MainWindow" Height="450" Width="800"' in markup and
            behind.count('InitializeComponent();')==1 and 'partial class MainWindow' in behind)


def read_owned(project):
    marker=project/'.avc_catalog.json'
    if not marker.exists(): return None
    doc=json.loads(marker.read_text(encoding='utf8'))
    if doc.get('mode')=='code':raise ValueError('Proyecto en modo código; regeneración del catálogo deshabilitada')
    actual={str(p.relative_to(project)):digest(p.read_bytes()) for p in project.rglob('*')
            if p.is_file() and p.suffix in ('.cs','.xaml','.csproj') and not set(p.relative_to(project).parts)&{'bin','obj','avc_publish'}}
    if actual!=doc['sources']:raise ValueError('El proyecto cambió fuera del catálogo. Se conserva sin sobrescribir; requiere edición de código.')
    return doc['plan']


def save_owned(project, plan):
    marker=project/'.avc_catalog.json'
    if marker.exists() and json.loads(marker.read_text(encoding='utf8')).get('mode')=='code':
        raise ValueError('No se permite sobrescribir extensiones de un proyecto en modo código')
    sources={str(p.relative_to(project)):digest(p.read_bytes()) for p in project.rglob('*')
             if p.is_file() and p.suffix in ('.cs','.xaml','.csproj') and not set(p.relative_to(project).parts)&{'bin','obj','avc_publish'}}
    (project/'.avc_catalog.json').write_text(json.dumps({'schema':1,'plan':plan,'sources':sources},ensure_ascii=False,indent=2),encoding='utf8')
