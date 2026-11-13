param(
    [string]$Source = '',
    [string]$BuildDir = 'D:\Codex\work\llm-rl-ppt-part2-20261112'
)
$ErrorActionPreference='Stop'
if(!$Source){$Source=Join-Path $PSScriptRoot '..\LLM-RL.pptx'}
$Source=(Resolve-Path -LiteralPath $Source).Path
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$backup=Join-Path $BuildDir 'before-part2.pptx'
if(!(Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $Source -Destination $backup}
$draft=Join-Path $BuildDir 'literature-and-sections-draft.pptx'
Copy-Item -LiteralPath $backup -Destination $draft -Force
function RGB([int]$r,[int]$g,[int]$b){return $r+256*$g+65536*$b}
$purple=RGB 104 11 122;$ink=RGB 30 32 40;$gray=RGB 103 97 109
$light=RGB 240 230 245;$rule=RGB 205 178 215
function Text($slide,$name,$content,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=17,[int]$color=$ink,[bool]$bold=$false){
    $s=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$s.Name=$name
    $s.TextFrame.MarginLeft=0;$s.TextFrame.MarginRight=0;$s.TextFrame.MarginTop=0;$s.TextFrame.MarginBottom=0
    $s.TextFrame.WordWrap=-1;$s.TextFrame.AutoSize=0
    $t=$s.TextFrame.TextRange;$t.Text=$content
    $t.Font.Name='Microsoft YaHei';$t.Font.NameFarEast='Microsoft YaHei'
    $t.Font.Size=$size;$t.Font.Color.RGB=$color;$t.Font.Bold=0
    if($bold){$t.Font.Bold=-1}
    $t.ParagraphFormat.SpaceBefore=0;$t.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0;$s.Width=$w;$s.Height=$h
    return $s
}
function Line($slide,$name,[double]$x1,[double]$y1,[double]$x2,[double]$y2,[int]$color=$rule,[double]$weight=1){
    $s=$slide.Shapes.AddLine($x1,$y1,$x2,$y2);$s.Name=$name;$s.Line.ForeColor.RGB=$color;$s.Line.Weight=$weight
    return $s
}
function BlankContent($deck,[int]$index,[string]$label){
    $range=$deck.Slides.Item(5).Duplicate();$slide=$range.Item(1);$slide.MoveTo($index)
    for($i=$slide.Shapes.Count;$i -ge 1;$i--){
        if($slide.Shapes.Item($i).Name -notin @('Group 2','Rectangle 1')){$slide.Shapes.Item($i).Delete()}
    }
    $null=$slide.Shapes.Item('Rectangle 1').TextFrame.TextRange.Replace('Background and Motivation',$label)
    $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=''
    return $slide
}
function Cover($deck,[int]$index,[int]$part,[string]$label){
    $range=$deck.Slides.Item(3).Duplicate();$slide=$range.Item(1);$slide.MoveTo($index)
    foreach($sh in $slide.Shapes){
        if($sh.Type -eq 6){
            foreach($child in $sh.GroupItems){
                if($child.HasTextFrame -eq -1){
                    $tr=$child.TextFrame.TextRange
                    if($tr.Text -match '^Part-'){$null=$tr.Replace('Part-01',('Part-{0:00}' -f $part))}
                    elseif($tr.Text -eq 'Background and Motivation'){$null=$tr.Replace('Background and Motivation',$label)}
                }
            }
        }
    }
    $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=''
    return $slide
}
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    if($d.Slides.Count -ne 6){throw 'Expected the six-slide user-approved first-part deck; inspect later changes before rebuilding.'}
    # Insert before the original closing slide; the first five slides stay intact.
    $null=Cover $d 6 2 'Literature Review'
    $s=BlankContent $d 7 'Literature Review'
    $mark=$s.Shapes.AddShape(1,25,71,9,31);$mark.Name='标题强调条';$mark.Fill.ForeColor.RGB=$purple;$mark.Line.Visible=0
    $null=Text $s '内容标题' 'LLM 在电力调度与强化学习中的应用' 46 67 884 43 28 $purple $true
    $null=Text $s '调度方向标题' '电力系统调度' 42 133 417 31 22 $purple $true
    $null=Text $s '强化学习方向标题' 'LLM 辅助 RL' 513 133 408 31 22 $purple $true
    $null=Line $s '调度方向横线' 42 175 447 175 $purple 1.2
    $null=Line $s '强化学习方向横线' 513 175 918 175 $purple 1.2
    $null=Line $s '两类文献分隔线' 482 136 482 413 $rule 0.8
    $leftTitles=@('自动建模与优化 [19]','拓扑自适应电压控制 [20]','PowerGraph-LLM [21]')
    $leftBodies=@(
        "多智能体分工：信息提取、`r问题建模与代码生成",
        "上下文学习构建潮流代理模型，`r结合 MPC 控制逆变器",
        "将电网图与表格编码为输入，`r学习求解最优潮流（OPF）"
    )
    $rightTitles=@('RL²：安全强化学习 [22]','GPT-Agent 与实时 OPF [23]','LLM 增强 RL 综述 [24]')
    $rightBodies=@(
        "理解安全要求，生成惩罚函数，`r根据 RL 表现迭代改进",
        "将自然语言规程解释为奖励与约束，`r融入原始-对偶 DRL 训练过程",
        "归纳信息处理、奖励设计、`r决策与生成四类作用"
    )
    for($i=0;$i -lt 3;$i++){
        $y=191+77*$i
        $null=Text $s ('调度文献标题'+$i) $leftTitles[$i] 42 $y 412 27 18 $ink $true
        $null=Text $s ('调度文献说明'+$i) $leftBodies[$i] 42 ($y+31) 414 43 16 $gray
        $null=Text $s ('RL文献标题'+$i) $rightTitles[$i] 513 $y 409 27 18 $ink $true
        $null=Text $s ('RL文献说明'+$i) $rightBodies[$i] 513 ($y+31) 413 43 16 $gray
    }
    $null=Line $s '本文位置横线' 42 435 918 435 $rule 1
    $null=Text $s '本文关注标签' '本文关注' 42 454 135 33 22 $purple $true
    $null=Text $s '本文协作思路' 'LLM 生成日前机械调度策略，与日内 RL 协作控制电压' 197 453 724 33 20 $purple $true
    $null=Text $s '策略改进思路' 'Reflexion 改进 LLM 策略，预训练与微调协调 RL 策略' 197 493 720 27 17 $gray
    $notes=@'
两列沿用目标论文引言的文献分组，编号与原文一致。左列覆盖调度建模、拓扑适应与OPF求解，右列覆盖安全惩罚、语言规程与通用综述。PowerGraph-LLM也直接学习OPF求解，综述也涵盖决策角色，因此不将这些工作一概描述成“仅作自然语言接口”。页底概括本论文的两阶段协作与策略改进重点。

[19] X. Yang et al., Large Language Model Powered Automated Modeling and Optimization of Active Distribution Network Dispatch Problems, IEEE TSG 17(2), 952–965, 2026. 作者预印本摘要：https://arxiv.org/abs/2507.21162
[20] A. Jena et al., LLM-Based Adaptive Distribution Voltage Regulation Under Frequent Topology Changes: An In-Context MPC Framework, IEEE TSG 16(5), 4297–4300, 2025. IEEE摘要：https://ieeexplore.ieee.org/document/11054276/ DOI:10.1109/TSG.2025.3583934
[21] F. Bernier et al., PowerGraph-LLM: Novel Power Grid Graph Embedding and Optimization With Large Language Models, IEEE TPWRS 40(6), 5483–5486, 2025. IEEE摘要：https://ieeexplore.ieee.org/document/11119316/ DOI:10.1109/TPWRS.2025.3596774
[22] X. Yang et al., RL2: Reinforce Large Language Model to Assist Safe Reinforcement Learning for Energy Management of Active Distribution Networks, IEEE TSG 16(4), 3419–3431, 2025. 作者预印本摘要：https://arxiv.org/abs/2412.01303
[23] Z. Yan and Y. Xu, Real-Time Optimal Power Flow With Linguistic Stipulations: Integrating GPT-Agent and Deep Reinforcement Learning, IEEE TPWRS 39(2), 4747–4750, 2024. IEEE摘要：https://doi.org/10.1109/TPWRS.2023.3338961
[24] Y. Cao et al., Survey on Large Language Model-Enhanced Reinforcement Learning: Concept, Taxonomy, and Methods, IEEE TNNLS 36(6), 9737–9757, 2025. 作者预印本摘要：https://arxiv.org/abs/2404.00282

本文位置来源：目标论文引言rc-000026–rc-000041，D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md。以上内容根据原论文书目及各文献作者/出版社摘要核对。
'@
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=$notes
    $labels=@('Model Formulation','Case Study','Teamwork','Conclusion and Future')
    for($i=0;$i -lt $labels.Count;$i++){
        $index=8+2*$i
        $null=Cover $d $index ($i+3) $labels[$i]
        $null=BlankContent $d ($index+1) $labels[$i]
    }
    $d.Save()
    $preview=Join-Path $BuildDir 'preview';New-Item -ItemType Directory -Path $preview -Force|Out-Null
    $audit=@()
    foreach($s in $d.Slides){
        $s.Export((Join-Path $preview ('slide-'+$s.SlideIndex+'.png')),'PNG',1600,900)
        $objects=@()
        foreach($sh in $s.Shapes){
            $items=@($sh)
            if($sh.Type -eq 6){$items=@();foreach($child in $sh.GroupItems){$items+=$child}}
            foreach($t in $items){
                if($t.HasTextFrame -eq -1 -and $t.TextFrame.HasText -eq -1){
                    $objects+=@{name=$t.Name;text=$t.TextFrame.TextRange.Text;left=$t.Left;top=$t.Top;width=$t.Width;height=$t.Height;boundWidth=$t.TextFrame.TextRange.BoundWidth;boundHeight=$t.TextFrame.TextRange.BoundHeight;fontSize=$t.TextFrame.TextRange.Font.Size}
                }
            }
        }
        $audit+=@{slide=$s.SlideIndex;shapeCount=$s.Shapes.Count;textObjects=$objects}
    }
    $audit|ConvertTo-Json -Depth 7|Set-Content -LiteralPath (Join-Path $BuildDir 'native-text-audit.json') -Encoding UTF8
    Write-Output ('Draft: '+$draft+'; slides='+$d.Slides.Count)
}finally{$d.Close()}
