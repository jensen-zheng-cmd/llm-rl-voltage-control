param(
    [string]$Source = (Join-Path $PSScriptRoot '..\LLM-RL.pptx'),
    [string]$BuildDir = 'D:\Codex\work\llm-rl-ppt-part1-20261112'
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
$Source = (Resolve-Path -LiteralPath $Source).Path
$backup = Join-Path $BuildDir 'LLM-RL-before-part1.pptx'
if (!(Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $Source -Destination $backup }
$draft = Join-Path $BuildDir 'LLM-RL-part1-draft.pptx'
Copy-Item -LiteralPath $backup -Destination $draft -Force

function RGB([int]$r,[int]$g,[int]$b) { return ($r + 256*$g + 65536*$b) }
$purple = RGB 104 11 122
$ink = RGB 38 38 38
$muted = RGB 100 96 104
$light = RGB 245 239 247
$lineColor = RGB 211 201 215
$red = RGB 176 53 61
$blue = RGB 48 104 153

function Text($slide,[string]$name,[string]$text,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=20,[int]$color=$ink,[bool]$bold=$false) {
    $s = $slide.Shapes.AddTextbox(1,$x,$y,$w,$h)
    $s.Name = $name
    $s.TextFrame.MarginLeft=0; $s.TextFrame.MarginRight=0
    $s.TextFrame.MarginTop=0; $s.TextFrame.MarginBottom=0
    $s.TextFrame.WordWrap=-1; $s.TextFrame.AutoSize=0
    $s.TextFrame.TextRange.Text=$text
    $s.TextFrame.TextRange.Font.Name='Microsoft YaHei'
    $s.TextFrame.TextRange.Font.NameFarEast='Microsoft YaHei'
    $s.TextFrame.TextRange.Font.Size=$size
    $s.TextFrame.TextRange.Font.Color.RGB=$color
    $s.TextFrame.TextRange.Font.Bold=0
    if($bold){$s.TextFrame.TextRange.Font.Bold=-1}
    $s.TextFrame.TextRange.ParagraphFormat.SpaceBefore=0
    $s.TextFrame.TextRange.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0
    $s.Width=$w; $s.Height=$h
    return $s
}
function Line($slide,[string]$name,[double]$x1,[double]$y1,[double]$x2,[double]$y2,[int]$color=$lineColor,[double]$weight=1.2,[bool]$arrow=$false) {
    $s=$slide.Shapes.AddLine($x1,$y1,$x2,$y2);$s.Name=$name
    $s.Line.ForeColor.RGB=$color;$s.Line.Weight=$weight
    if($arrow){$s.Line.EndArrowheadStyle=3}
    return $s
}
function Rect($slide,[string]$name,[double]$x,[double]$y,[double]$w,[double]$h,[int]$fill=$light) {
    $s=$slide.Shapes.AddShape(1,$x,$y,$w,$h);$s.Name=$name
    $s.Fill.ForeColor.RGB=$fill;$s.Line.Visible=0
    return $s
}
function Heading($slide,[string]$text) {
    $null=Text $slide 'Content title' $text 40 82 880 42 26 $purple $true
    $null=Line $slide 'Title underline' 40 133 920 133 $lineColor 1
}
function Takeaway($slide,[string]$text) {
    $null=Line $slide 'Takeaway rule' 40 461 920 461 $lineColor 1
    $null=Text $slide 'Takeaway' $text 40 478 870 32 20 $purple $true
}
function Notes($slide,[string]$text) {
    $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=$text
}

$app=New-Object -ComObject PowerPoint.Application
$deck=$app.Presentations.Open($draft,0,0,0)
try {
    if($deck.Slides.Count -ne 5){throw 'Expected the original five-slide source deck.'}
    # The existing agenda artwork, section divider, and content header are retained.
    $agenda=$deck.Slides.Item(2)
    for($i=$agenda.Shapes.Count;$i -ge 1;$i--){if($agenda.Shapes.Item($i).Name -ne 'TextBox 1'){$agenda.Shapes.Item($i).Delete()}}
    $entries=@('Background and Motivation','Literature Review','Model Formulation','Case Study','Teamwork','Conclusion and Future')
    for($i=0;$i -lt $entries.Count;$i++){
        $y=91+$i*66
        $null=Text $agenda ('Agenda number '+($i+1)) ('{0:00}' -f ($i+1)) 405 $y 50 38 28 $purple $true
        $null=Text $agenda ('Agenda title '+($i+1)) $entries[$i] 472 ($y+3) 460 36 24 $ink $true
    }
    Notes $agenda '汇报分为六部分：研究背景与动机、相关文献、模型与方法、复现实验、团队分工、总结与展望。'
    # Duplicate the exact slide 4, preserving its master, logo, top band, and page field.
    for($i=0;$i -lt 3;$i++) { $r=$deck.Slides.Item(4).Duplicate(); $r.Item(1).MoveTo(5) }

    $s=$deck.Slides.Item(4)
    Heading $s '分布式光伏接入，带来电压调节需求'
    $null=Text $s 'PV mechanism heading' '光伏波动与末端电压' 43 159 405 32 22 $purple $true
    $null=Text $s 'PV mechanism explanation' "分布式光伏出力具有间歇性和随机性`r高出力时段可能出现反向潮流与过电压" 43 208 414 64 19
    $null=Text $s 'PV mechanism operation' "负荷与光伏持续变化`r需要协调资源，抑制电压偏差与越限" 43 314 414 65 19
    $null=Line $s 'Columns separator' 482 159 482 420 $lineColor 1
    $null=Text $s 'Resources heading' '主动配电网中的两类调节资源' 520 159 400 32 22 $purple $true
    $null=Text $s 'Slow timescale' '日前调度' 521 211 110 32 20 $purple $true
    $null=Text $s 'Slow assets' '有载调压变压器 OLTC / 并联电容器 SC' 521 253 405 28 18 $ink $true
    $null=Text $s 'Slow description' '机械动作受限，提前安排档位与投切时段' 521 292 402 28 18 $muted
    $null=Line $s 'Resources row rule' 521 334 919 334 $lineColor 1
    $null=Text $s 'Fast timescale' '日内控制' 521 354 110 30 20 $purple $true
    $null=Text $s 'Fast assets' '光伏逆变器无功出力' 657 355 262 28 18 $ink $true
    $null=Text $s 'Fast description' '依据实时量测快速修正末端电压' 521 399 400 27 18 $muted
    Takeaway $s '控制任务：机械设备提前调度，逆变器实时精细调节'
    Notes $s "光伏接入以后，配电网的功率流向和电压分布会随出力变化。高光伏时段可能发生反向潮流和末端过电压，所以需要主动协调可控资源。这里区分两类设备：OLTC和SC存在机械动作与时间耦合约束，需要提前安排；光伏逆变器可以利用剩余容量快速调节无功，依据实时量测修正末端电压。两类设备的时间尺度不同，是两阶段控制的物理基础。`r`r来源：论文引言 rc-000010–rc-000011、两阶段问题 rc-000067–rc-000069；D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md。"

    $s=$deck.Slides.Item(5)
    Heading $s '模型与可用信息，决定控制方法的适用条件'
    $null=Text $s 'Models label' '模型层面' 43 163 160 31 22 $purple $true
    $null=Text $s 'Models requirement' '传统优化依赖拓扑、线路参数和设备模型' 223 164 690 31 21 $ink $true
    $null=Text $s 'Models difficulty' '细粒度模型的获取与持续维护较困难' 223 209 690 30 19 $muted
    $null=Line $s 'Model row rule' 43 254 919 254 $lineColor 1
    $null=Text $s 'Information label' '预测层面' 43 277 160 31 22 $purple $true
    $null=Text $s 'Information issue' '日前以区域级、逐小时预测为主' 223 278 690 31 21 $ink $true
    $null=Text $s 'Information heterogeneity' '还需结合运行规程、历史记录和电压报告' 223 323 690 30 19 $muted
    $null=Line $s 'Information row rule' 43 365 919 365 $lineColor 1
    $null=Text $s 'RL motivation label' '数据驱动' 43 390 160 31 22 $purple $true
    $null=Text $s 'RL motivation' 'RL 通过交互学习控制策略，降低在线决策的模型依赖' 223 389 690 32 20 $ink $true
    Takeaway $s '新的问题：如何让控制策略利用粗粒度预测和非结构化信息？'
    Notes $s "选择数据驱动方法，首先与信息条件有关。传统优化需要明确的网络拓扑、参数与设备模型，实际维护这些信息存在成本。日前阶段通常只能拿到区域级、逐小时预测，而不是准确的节点级预测。同时，运行决策还涉及历史记录和文字规程。RL可以从环境交互中学习控制规律，降低在线决策对显式物理模型的依赖，但结构化数值状态也不直接覆盖这些异构信息。这引出了论文对LLM信息整合能力的需求。我们的复现训练仍然使用交流潮流仿真环境。`r`r来源：论文引言 rc-000012、rc-000019–rc-000020，协作框架 rc-000086。复现配置：configs/system_v2.json、configs/environment_v1.json。"

    $s=$deck.Slides.Item(6)
    Heading $s '日前机械设备调度，需要理解运行规则与临时指令'
    $null=Text $s 'Rules heading' '运行规程' 43 160 190 33 22 $purple $true
    $null=Text $s 'Rules example 1' '“OLTC 每日调节次数受上限约束”' 43 216 419 33 21 $ink $true
    $null=Text $s 'Rules example 2' '“SC 只能在指定时间窗口内投入”' 43 267 419 33 21 $ink $true
    $null=Text $s 'Rules implication' "影响整日动作序列`r需要满足时间耦合约束" 43 335 400 63 19 $muted
    $null=Line $s 'Scenario columns separator' 484 160 484 417 $lineColor 1
    $null=Text $s 'Outage heading' '检修与停运指令' 522 160 394 33 22 $purple $true
    $null=Text $s 'Outage example' ('“明日某台 SC 检修，'+"`r"+'全天不得投入”') 522 216 397 72 22 $ink $true
    $null=Text $s 'Outage implication' "运行条件临时变化`r需要据此调整日前计划" 522 335 397 63 19 $muted
    $null=Text $s 'Frozen RL adaptation' '冻结的 RL 策略，需要预先设计约束处理与适配机制' 43 420 870 27 18 $muted
    Takeaway $s '语言理解能力，可以帮助调度策略接收规程与临时变化'
    Notes $s "日前控制面对的并不只有数值预测。OLTC有每日动作次数约束，SC有可投入的时间窗口，还可能临时接到检修停运通知。对于冻结的数值RL策略，如果状态、动作空间和约束处理机制没有提前覆盖这些变化，就需要额外设计。常用处理包括奖励惩罚、约束策略优化或动作掩码。LLM的作用是理解这些文字信息，并在生成日前计划时一并考虑。这里的动作次数与时间窗也能写进混合整数优化，论文动机侧重语言信息的接收与适应。`r`r来源：论文引言 rc-000021–rc-000024、两阶段问题 rc-000067。"

    $s=$deck.Slides.Item(7)
    Heading $s '相同的标量反馈，可能对应相反的调节方向'
    $null=Text $s 'Reward explanation' '示例：只按电压偏差评价，r = −|V − 1|' 43 160 870 34 22 $ink $true
    # A native editable voltage scale, explicitly illustrative rather than measured data.
    $null=Line $s 'Voltage baseline' 138 279 820 279 $lineColor 2
    foreach($x in @(153,479,805)){$null=Line $s ('Voltage tick '+$x) $x 270 $x 288 $muted 1.5}
    $null=Text $s 'Undervoltage value' '0.95 p.u.' 87 224 166 36 27 $blue $true
    $null=Text $s 'Voltage reference' '1.00 p.u.' 408 224 166 36 27 $muted $true
    $null=Text $s 'Overvoltage value' '1.05 p.u.' 738 224 166 36 27 $red $true
    $null=Line $s 'Raise voltage arrow' 166 279 364 279 $blue 3 $true
    $null=Line $s 'Lower voltage arrow' 792 279 594 279 $red 3 $true
    $null=Text $s 'Undervoltage action' '偏差 0.05，需要升压' 69 309 316 31 20 $blue $true
    $null=Text $s 'Overvoltage action' '偏差 0.05，需要降压' 611 309 316 31 20 $red $true
    $null=Text $s 'Rich feedback statement' '引入电压曲线和运行记录，给策略改进提供更具体的反馈' 43 393 875 34 21 $ink $true
    Takeaway $s 'LLM 负责日前信息整合与计划生成，RL 负责日内快速控制'
    Notes $s "最后一个动机是策略改进的效率。这里是简化示例：如果只看电压偏差，0.95和1.05都与参考值相差0.05，会得到相同的标量评价，但前者需要升压，后者需要降压。RL可以通过状态和环境交互学到这种关系，论文指出这个学习过程可能需要较多试错。因此，LLM的离线改进除了奖励，还接收电压曲线等更具体的反馈，用于提出下一轮日前计划。结合前几页的预测与语言信息需求，论文把日前机械调度交给LLM，把日内快速无功控制交给RL。接下来介绍相关工作。`r`r来源：论文引言 rc-000025–rc-000027、LLM作为决策模型 rc-000072；本文奖励只是简化解释示例，不代表论文或复现的完整奖励函数。"

    $deck.Save()
    $preview=Join-Path $BuildDir 'preview'
    New-Item -ItemType Directory -Path $preview -Force | Out-Null
    $audit=@()
    foreach($s in $deck.Slides){
        $s.Export((Join-Path $preview ('slide-'+$s.SlideIndex.ToString('00')+'.png')),'PNG',1600,900)
        $objects=@()
        foreach($sh in $s.Shapes){
            if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){
                $objects+=@{name=$sh.Name;text=$sh.TextFrame.TextRange.Text;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height;boundHeight=$sh.TextFrame.TextRange.BoundHeight;boundWidth=$sh.TextFrame.TextRange.BoundWidth;fontSize=$sh.TextFrame.TextRange.Font.Size}
            }
        }
        $audit+=@{slide=$s.SlideIndex;shapeCount=$s.Shapes.Count;textObjects=$objects}
    }
    $audit | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath (Join-Path $BuildDir 'native-text-audit.json') -Encoding utf8
    Write-Output ('Draft: '+$draft+'; slides: '+$deck.Slides.Count)
} finally { $deck.Close() }
