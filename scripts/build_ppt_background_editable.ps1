param(
    [string]$Source = '',
    [string]$BuildDir = 'D:\Codex\work\llm-rl-ppt-part1-editable-20261112',
    [string]$PreviewImage = ''
)
$ErrorActionPreference='Stop'
if(!$Source){$Source=Join-Path $PSScriptRoot '..\LLM-RL.pptx'}
if(!$PreviewImage){$PreviewImage=Join-Path $PSScriptRoot '..\results\class-report-v2\assets\approved-background-preview.png'}
$Source=(Resolve-Path -LiteralPath $Source).Path
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
$backup=Join-Path $BuildDir 'before-editable.pptx'
if(!(Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $Source -Destination $backup}
$draft=Join-Path $BuildDir 'editable-background-draft.pptx'
Copy-Item -LiteralPath $backup -Destination $draft -Force

function RGB([int]$r,[int]$g,[int]$b){return $r+256*$g+65536*$b}
$purple=RGB 104 11 122; $ink=RGB 30 32 40; $lavender=RGB 236 222 242
$muted=RGB 102 96 108; $pale=RGB 249 246 251; $rule=RGB 196 150 207
$blue=RGB 0 111 182; $amber=RGB 211 144 18; $white=16777215

function Text($slide,$name,$content,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=16,[int]$color=$ink,[bool]$bold=$false,[int]$align=1){
    $sh=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$sh.Name=$name
    $sh.TextFrame.MarginLeft=0;$sh.TextFrame.MarginRight=0
    $sh.TextFrame.MarginTop=0;$sh.TextFrame.MarginBottom=0
    $sh.TextFrame.WordWrap=-1;$sh.TextFrame.AutoSize=0
    $t=$sh.TextFrame.TextRange;$t.Text=$content
    $t.Font.Name='Microsoft YaHei';$t.Font.NameFarEast='Microsoft YaHei'
    $t.Font.Size=$size;$t.Font.Color.RGB=$color;$t.Font.Bold=0
    if($bold){$t.Font.Bold=-1}
    $t.ParagraphFormat.Alignment=$align
    $t.ParagraphFormat.SpaceBefore=0;$t.ParagraphFormat.SpaceAfter=0
    $sh.TextFrame2.AutoSize=0;$sh.Width=$w;$sh.Height=$h
    return $sh
}
function Shape($slide,$name,[int]$kind,[double]$x,[double]$y,[double]$w,[double]$h,[int]$fill=$white,[int]$stroke=$purple,[double]$weight=1){
    $sh=$slide.Shapes.AddShape($kind,$x,$y,$w,$h);$sh.Name=$name
    $sh.Fill.ForeColor.RGB=$fill;$sh.Line.ForeColor.RGB=$stroke;$sh.Line.Weight=$weight
    if($stroke -lt 0){$sh.Line.Visible=0}
    if($kind -eq 5){$sh.Adjustments.Item(1)=0.035}
    return $sh
}
function Line($slide,$name,[double]$x1,[double]$y1,[double]$x2,[double]$y2,[int]$color=$purple,[double]$weight=1.5,[bool]$arrow=$false){
    $sh=$slide.Shapes.AddLine($x1,$y1,$x2,$y2);$sh.Name=$name
    $sh.Line.ForeColor.RGB=$color;$sh.Line.Weight=$weight
    if($arrow){$sh.Line.EndArrowheadStyle=3}
    return $sh
}
function Poly($slide,$name,$points,[int]$color=$purple,[double]$weight=1.5,[bool]$arrow=$false,[int]$fill=-1){
    $first=$points[0];$builder=$slide.Shapes.BuildFreeform(0,[single]$first[0],[single]$first[1])
    for($i=1;$i -lt $points.Count;$i++){$builder.AddNodes(0,0,[single]$points[$i][0],[single]$points[$i][1])}
    $sh=$builder.ConvertToShape();$sh.Name=$name
    $sh.Line.ForeColor.RGB=$color;$sh.Line.Weight=$weight
    $sh.Fill.Visible=0
    if($fill -ge 0){$sh.Fill.Visible=-1;$sh.Fill.ForeColor.RGB=$fill}
    if($arrow){$sh.Line.EndArrowheadStyle=3}
    return $sh
}
function Title($slide,$title){
    $null=Shape $slide '标题强调条' 1 25 71 9 31 $purple -1
    $null=Text $slide '内容标题' $title 46 67 887 43 28 $purple $true
}
function Tab($slide,$name,$label,$x,$y,$w){
    $null=Shape $slide ($name+'浅紫底') 5 ($x+10) $y $w 28 $lavender -1
    $null=Shape $slide ($name+'紫色底') 5 $x $y $w 28 $purple -1
    $null=Text $slide $name $label ($x+6) ($y+2) ($w-12) 25 19 $white $true 2
}
function CropAsset($slide,$name,$path,$crop,[double]$x,[double]$y,[double]$w,[double]$h){
    # Native PowerPoint cropping keeps the photo as a separately selectable picture.
    $sh=$slide.Shapes.AddPicture($path,0,-1,0,0,-1,-1);$sh.Name=$name
    $sh.LockAspectRatio=0
    $cf=$sh.PictureFormat.Crop
    $cf.ShapeLeft=$x;$cf.ShapeTop=$y;$cf.ShapeWidth=$w;$cf.ShapeHeight=$h
    $cf.PictureWidth=$w/$crop[2];$cf.PictureHeight=$h/$crop[3]
    $cf.PictureOffsetX=(0.5-$crop[0]-$crop[2]/2)*$cf.PictureWidth
    $cf.PictureOffsetY=(0.5-$crop[1]-$crop[3]/2)*$cf.PictureHeight
    return $sh
}
function CleanBody($slide){
    for($i=$slide.Shapes.Count;$i -ge 1;$i--){
        $sh=$slide.Shapes.Item($i)
        if($sh.Name -notin @('Group 2','Rectangle 1')){$sh.Delete()}
    }
}
function Emphasize($shape,[string]$phrase){
    $r=$shape.TextFrame.TextRange;$pos=$r.Text.IndexOf($phrase)
    if($pos -ge 0){$r.Characters($pos+1,$phrase.Length).Font.Bold=-1}
}

$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    if($d.Slides.Count -ne 6){throw 'Expected the user-revised six-slide deck; inspect any later revisions before rebuilding.'}
    $beforeDir=Join-Path $BuildDir 'before';New-Item -ItemType Directory -Path $beforeDir -Force | Out-Null
    foreach($sl in $d.Slides){$sl.Export((Join-Path $beforeDir ('slide-'+$sl.SlideIndex+'.png')),'PNG',1600,900)}
    $s=$d.Slides.Item(4);CleanBody $s;Title $s '分布式光伏接入下的电压控制'
    # Connections are placed first so their junctions stay behind the panels.
    $null=Poly $s '产业背景到电压挑战' @(@(257,270),@(274,270),@(274,194),@(298,194)) $purple 1.8 $true
    $null=Poly $s '产业背景到可调资源' @(@(257,270),@(274,270),@(274,353),@(298,353)) $purple 1.8 $true
    $null=Line $s '模型条件推动数据驱动' 517 194 688 194 $purple 1.9 $true
    $null=Line $s '控制需求推动数据驱动' 517 353 688 353 $purple 1.9 $true
    $null=Text $s '模型获取维护条件' '细粒度模型获取与维护困难' 523 167 162 23 12.8 $purple $true 2
    $null=Text $s '控制协同需求' '多资源协同控制需求提高' 522 326 164 23 13.2 $purple $true 2
    $null=Poly $s '产业与应用汇合' @(@(144,417),@(144,437),@(816,437),@(816,417)) $purple 1.7
    $null=Line $s '矛盾引出箭头' 480 437 480 459 $purple 2 $true

    $null=Shape $s '产业背景框' 5 25 151 233 266 $white $purple 1
    Tab $s '产业背景标签' '产业背景' 30 123 119
    $null=Text $s '新能源接入' "可再生能源接入`r尤其是分布式光伏" 47 171 207 60 21 $ink $true
    $null=Line $s '产业标题短线' 49 235 103 235 $purple 1
    $null=CropAsset $s '屋顶分布式光伏配图' $PreviewImage @(0.0341,0.4602,0.2261,0.2763) 33 249 216 150

    $null=Shape $s '电压控制挑战框' 5 298 121 219 149 $white $purple 1
    $null=Shape $s '电压挑战标题底' 5 311 124 164 25 $lavender -1
    $null=Text $s '电压挑战标题' '电压控制挑战' 331 124 175 26 18 $purple $true
    $null=Text $s '光伏波动描述' "间歇性与随机性`r电压波动、越限风险增加" 320 156 194 41 14.2 $ink $true
    # Voltage trace is an editable qualitative illustration, not measured data.
    $null=Line $s '电压纵轴' 337 256 337 211 $ink 0.8 $true
    $null=Line $s '电压横轴' 337 256 503 256 $ink 0.8 $true
    $null=Text $s '电压单位' 'V (p.u.)' 338 197 58 16 8.5 $ink
    $null=Text $s '时间符号' 't' 504 250 10 13 8.5 $ink
    foreach($pair in @(@(218,'1.05'),@(235,'1.00'),@(252,'0.95'))){
        $yy=[double]$pair[0];$guide=Line $s ('电压参考线'+$pair[1]) 337 $yy 498 $yy (RGB 159 155 165) 0.7
        $guide.Line.DashStyle=4
        $null=Text $s ('电压刻度'+$pair[1]) $pair[1] 311 ($yy-6) 24 13 8.2 $ink
    }
    $trace=@(@(339,233),@(344,229),@(348,236),@(352,234),@(357,240),@(361,232),@(367,234),@(373,225),@(379,239),@(384,236),@(389,242),@(393,233),@(399,237),@(404,224),@(409,232),@(414,218),@(418,226),@(423,213),@(427,221),@(432,230),@(438,228),@(444,235),@(450,231),@(455,241),@(461,238),@(467,245),@(472,231),@(477,236),@(481,227),@(485,235),@(489,226),@(493,231),@(498,229))
    $null=Poly $s '电压波动示意曲线' $trace $amber 1.6

    $null=Shape $s '可调资源框' 5 298 280 219 140 $white $purple 1
    $null=Shape $s '可调资源标题底' 5 311 283 164 25 $lavender -1
    $null=Text $s '可调资源标题' '可调节资源增加' 331 283 177 26 18 $purple $true
    $null=Text $s '主动配电网描述' "光伏逆变器无功可调`r被动配电网转向主动配电网" 320 315 194 40 13.6 $ink $true
    $null=CropAsset $s '光伏逆变器配图' $PreviewImage @(0.341,0.655,0.064,0.110) 328 356 54 57
    $null=Line $s '无功能力纵轴' 420 405 420 360 $ink 0.8 $true
    $null=Line $s '无功能力横轴' 420 405 493 405 $ink 0.8 $true
    $null=Text $s '无功符号' 'Q' 407 351 13 17 10 $ink
    $null=Text $s '有功符号' 'P' 494 398 13 17 10 $ink
    $curve=@(@(420,364),@(429,364),@(438,366),@(448,369),@(458,375),@(469,383),@(480,394),@(486,404),@(420,404),@(420,364))
    $null=Poly $s '逆变器无功能力示意' $curve $blue 1.2 $false (RGB 210 232 251)

    $null=Shape $s '数据驱动应用框' 5 688 151 247 266 $white $purple 1
    Tab $s '应用背景标签' '应用背景' 692 123 119
    $null=Text $s '数据驱动主题' '数据驱动电压控制' 706 172 220 30 21 $ink $true
    $null=Text $s '深度强化学习描述' '以深度强化学习（RL）为代表' 706 207 220 25 16.4
    $null=Shape $s '低模型依赖强调底' 5 741 240 146 31 (RGB 255 237 190) -1
    $null=Text $s '低模型依赖强调' '低模型依赖性' 744 242 140 26 18 (RGB 139 94 9) $true 2
    # The learning loop consists of independent editable nodes and connectors.
    $null=Poly $s '动作交互连线' @(@(740,315),@(740,294),@(881,294),@(881,315)) $purple 1.8 $true
    $null=Poly $s '反馈交互连线' @(@(881,367),@(881,388),@(740,388),@(740,367)) $purple 1.8 $true
    $null=Shape $s 'RL智能体节点' 5 698 315 104 54 (RGB 226 240 253) $blue 0.9
    $null=Shape $s '配电网环境节点' 5 829 315 98 54 (RGB 255 242 213) $amber 0.9
    $null=Text $s 'RL智能体文字' 'RL 智能体' 701 342 98 23 15.5 $ink $true 2
    $null=Text $s '配电网环境文字' '配电网环境' 831 342 94 23 15.5 $ink $true 2
    # Compact editable symbols communicate agent and grid, matching the approved preview.
    $null=Shape $s '智能体符号头' 5 739 321 21 15 (RGB 226 240 253) $blue 1.4
    $null=Shape $s '智能体符号左眼' 9 744 326 2.5 2.5 $blue -1
    $null=Shape $s '智能体符号右眼' 9 753 326 2.5 2.5 $blue -1
    $null=Line $s '智能体天线' 749 321 749 317 $blue 1.3
    $null=Poly $s '电网铁塔符号' @(@(868,337),@(878,319),@(888,337)) (RGB 151 106 19) 1.2
    $null=Line $s '电网铁塔横担一' 870 325 886 325 (RGB 151 106 19) 1.2
    $null=Line $s '电网铁塔横担二' 866 330 890 330 (RGB 151 106 19) 1.2

    $null=Shape $s '进一步矛盾底' 5 27 465 907 62 $lavender -1
    $null=Text $s '进一步矛盾标签' '进一步的矛盾' 48 484 229 32 22 $purple $true 2
    $null=Line $s '矛盾信息分隔线' 300 477 300 518 $purple 1
    $null=Text $s '日前信息需求' '日前决策需要结合预测、运行规程与历史记录' 334 470 588 25 18 $ink $true
    $null=Shape $s '信息利用难点底' 5 330 496 306 27 (RGB 255 237 190) -1
    $null=Text $s '信息利用难点' 'RL 难以利用非结构化信息' 340 497 568 25 19 $purple $true
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='光伏接入同时带来波动性与可控资源。模型获取维护成本与更高的协同控制需求，推动以RL为代表的数据驱动方法。但日前决策还需要预测、规程与历史记录，引出下一页四个挑战。电压与P-Q图仅作定性示意。配图来自用户已确认的内置生图预览，正文、示意曲线及流程图均由PowerPoint原生对象重建。来源：论文引言rc-000010–rc-000025。'

    $s=$d.Slides.Item(5);CleanBody $s;Title $s '日前电压控制中的四个关键挑战'
    $headings=@('信息不完整且异构',"基于语义的`r运行约束","适应长尾需求的`r指令",'策略改进方向模糊')
    $bodies=@(
        @('日前阶段仅能获得粗粒度信息，即逐小时的区域级预测。','日前策略不仅依赖结构化数据，还依赖历史记录和电压报告等非结构化信息。'),
        @('“OLTC的每日调节次数受上限约束”“SCs每天只能在特定时间窗口内投入”。','数据驱动的RL方法难以理解和处理此类约束。'),
        @('OLTC和SCs等机械资产在实际运行中可能经历检修或停运。','传统RL方法既无法有效处理和解释突发的自然语言指令，也无法做出必要的调整。'),
        @('传统RL中的奖励仅仅是一个包含不足物理信息的标量值。','1.05 p.u.和0.95 p.u.的电压水平可能产生相同的奖励值。')
    )
    $ys=@(140,240,340,440)
    for($i=0;$i -lt 4;$i++){
        $y=$ys[$i]
        $null=Text $s ('挑战编号'+($i+1)) ('{0:00}' -f ($i+1)) 31 ($y-2) 68 57 45 (RGB 185 130 198) $true
        $null=Line $s ('编号竖线'+($i+1)) 108 ($y+3) 108 ($y+47) $purple 1
        $hy=$y+8;if($i -eq 1 -or $i -eq 2){$hy=$y-1}
        $null=Text $s ('挑战标题'+($i+1)) $headings[$i] 128 $hy 202 61 21 $purple $true
        $first=Text $s ('挑战说明'+($i+1)+'a') $bodies[$i][0] 341 ($y-2) 593 43 15.3
        $second=Text $s ('挑战说明'+($i+1)+'b') $bodies[$i][1] 341 ($y+28) 593 50 15.3
        foreach($key in @('粗粒度信息','非结构化信息','自然语言指令','标量值')){Emphasize $first $key;Emphasize $second $key}
        if($i -lt 3){$null=Line $s ('四项分隔线'+($i+1)) 25 ($y+76) 936 ($y+76) $rule 0.9}
    }
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='四项挑战依次摘自论文引言rc-000019–rc-000025：信息不完整且异构、基于语义的运行约束、适应长尾需求的指令、策略改进方向模糊。运行约束两条例子作简短节选，其余句子按中文原文摘录。解释最后一项时，可补充0.95与1.05都偏离参考值0.05，但需要相反的电压调节方向。来源：D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md。'

    $d.Save()
    $previewDir=Join-Path $BuildDir 'preview';New-Item -ItemType Directory -Path $previewDir -Force | Out-Null
    $audit=@()
    foreach($sl in $d.Slides){
        $sl.Export((Join-Path $previewDir ('slide-'+$sl.SlideIndex+'.png')),'PNG',1600,900)
        $objects=@()
        foreach($sh in $sl.Shapes){
            $record=@{name=$sh.Name;type=$sh.Type;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height}
            if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){
                $record.text=$sh.TextFrame.TextRange.Text;$record.boundHeight=$sh.TextFrame.TextRange.BoundHeight
                $record.boundWidth=$sh.TextFrame.TextRange.BoundWidth;$record.fontSize=$sh.TextFrame.TextRange.Font.Size
            }
            $objects+=$record
        }
        $audit+=@{slide=$sl.SlideIndex;shapeCount=$sl.Shapes.Count;objects=$objects}
    }
    $audit|ConvertTo-Json -Depth 7|Set-Content -LiteralPath (Join-Path $BuildDir 'native-object-audit.json') -Encoding UTF8
    Write-Output ('Draft: '+$draft+'; slides='+$d.Slides.Count)
}finally{$d.Close()}
