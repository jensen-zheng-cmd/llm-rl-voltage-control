param(
    [string]$Source='D:\Download\A_storage\新海风\复现：LLM-RL调度论文\LLM-RL.pptx',
    [string]$BuildDir='D:\Codex\work\llm-rl-ppt-pages19-24-20261113'
)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$snapshot=Join-Path $BuildDir 'source-snapshot.pptx'
if(-not (Test-Path -LiteralPath $snapshot)){Copy-Item -LiteralPath $Source -Destination $snapshot}
function SharedHash($path){
    $stream=[System.IO.FileStream]::new($path,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite)
    $sha=[System.Security.Cryptography.SHA256]::Create()
    try{return [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}finally{$sha.Dispose();$stream.Dispose()}
}
$sourceHash=SharedHash $Source
$purple=104+256*11+65536*122
function Text($slide,$name,$content,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=17,[bool]$bold=$false){
    $sh=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$sh.Name=$name
    $sh.TextFrame.MarginLeft=0;$sh.TextFrame.MarginRight=0;$sh.TextFrame.MarginTop=0;$sh.TextFrame.MarginBottom=0
    $sh.TextFrame.AutoSize=0;$sh.TextFrame.WordWrap=-1
    $t=$sh.TextFrame.TextRange;$t.Text=$content
    $t.Font.Name='Microsoft YaHei';$t.Font.NameFarEast='Microsoft YaHei';$t.Font.Size=$size
    $t.Font.Bold=0;$t.Font.Color.RGB=2500134
    if($bold){$t.Font.Bold=-1;$t.Font.Color.RGB=$purple}
    $t.ParagraphFormat.SpaceBefore=0;$t.ParagraphFormat.SpaceAfter=0
    $sh.TextFrame2.AutoSize=0;$sh.Width=$w;$sh.Height=$h
    return $sh
}
function EvidenceTable($slide,$name,$rows,[double]$y){
    $shape=$slide.Shapes.AddTable(10,5,49,$y,397,164);$shape.Name=$name
    $table=$shape.Table
    $table.Columns.Item(1).Width=107
    for($c=2;$c -le 5;$c++){$table.Columns.Item($c).Width=72.5}
    for($r=1;$r -le 10;$r++){
        $table.Rows.Item($r).Height=16.4
        for($c=1;$c -le 5;$c++){
            $cell=$table.Cell($r,$c)
            $cell.Shape.Fill.Visible=-1;$cell.Shape.Fill.Solid();$cell.Shape.Fill.ForeColor.RGB=16777215
            foreach($edge in 1..4){$cell.Borders.Item($edge).Visible=0}
            $tf=$cell.Shape.TextFrame;$tf.MarginLeft=0;$tf.MarginRight=0;$tf.MarginTop=0;$tf.MarginBottom=0
            $tf.VerticalAnchor=3
            $t=$tf.TextRange;$t.Font.Name='Times New Roman';$t.Font.Size=12
            $t.Font.Bold=0;$t.Font.Color.RGB=0;$t.ParagraphFormat.Alignment=2
            $t.ParagraphFormat.SpaceBefore=0;$t.ParagraphFormat.SpaceAfter=0
        }
    }
    $table.Cell(1,1).Merge($table.Cell(2,1))
    $table.Cell(1,2).Merge($table.Cell(1,3))
    $table.Cell(1,4).Merge($table.Cell(1,5))
    $table.Cell(1,1).Shape.TextFrame.TextRange.Text='Method'
    $table.Cell(1,2).Shape.TextFrame.TextRange.Text='Voltage Deviation (p.u.)'
    $table.Cell(1,4).Shape.TextFrame.TextRange.Text='Violation Rate (%)'
    foreach($c in 2,3,4,5){$table.Cell(2,$c).Shape.TextFrame.TextRange.Text=@('Mean','Std.')[($c-2)%2]}
    for($r=3;$r -le 10;$r++){
        for($c=1;$c -le 5;$c++){
            $table.Cell($r,$c).Shape.TextFrame.TextRange.Text=[string]$rows[$r-3][$c-1]
            if(($r -eq 6 -and $c -gt 1) -or ($r -eq 9 -and $c -eq 3)){$table.Cell($r,$c).Shape.TextFrame.TextRange.Font.Bold=-1}
        }
    }
    foreach($c in 1,2,4){$b=$table.Cell(1,$c).Borders.Item(1);$b.Visible=-1;$b.ForeColor.RGB=0;$b.Weight=[single]1.1}
    foreach($c in 2,4){$b=$table.Cell(1,$c).Borders.Item(3);$b.Visible=-1;$b.ForeColor.RGB=0;$b.Weight=[single]0.5}
    for($c=1;$c -le 5;$c++){
        if($c -eq 1){$b=$table.Cell(1,$c).Borders.Item(3)}else{$b=$table.Cell(2,$c).Borders.Item(3)}
        $b.Visible=-1;$b.ForeColor.RGB=0;$b.Weight=[single]0.65
        $b=$table.Cell(10,$c).Borders.Item(3);$b.Visible=-1;$b.ForeColor.RGB=0;$b.Weight=[single]1.1
    }
    return $shape
}
function Audit($d,$path){
    $items=@()
    foreach($sh in $d.Slides.Item(1).Shapes){
        $obj=@{name=$sh.Name;type=$sh.Type;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height}
        if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){
            $obj.text=$sh.TextFrame.TextRange.Text;$obj.boundWidth=$sh.TextFrame.TextRange.BoundWidth;$obj.boundHeight=$sh.TextFrame.TextRange.BoundHeight
            if($sh.Name -like '说明*' -or $sh.Name -like '正文*' -or $sh.Name -like '表格*'){
                if($obj.boundHeight -gt $sh.Height+0.5 -or $obj.boundWidth -gt $sh.Width+0.5){throw ('Text overflow: '+$sh.Name)}
            }
        }
        if($sh.HasTable -eq -1){
            $obj.cells=@()
            for($r=1;$r -le $sh.Table.Rows.Count;$r++){
                for($c=1;$c -le $sh.Table.Columns.Count;$c++){
                    $cell=$sh.Table.Cell($r,$c).Shape
                    $obj.cells+=@{row=$r;column=$c;text=$cell.TextFrame.TextRange.Text;width=$cell.Width;height=$cell.Height;boundWidth=$cell.TextFrame.TextRange.BoundWidth;boundHeight=$cell.TextFrame.TextRange.BoundHeight}
                    if($cell.TextFrame.TextRange.BoundWidth -gt $cell.Width+0.5 -or $cell.TextFrame.TextRange.BoundHeight -gt $cell.Height+0.5){throw ('Table text overflow: '+$sh.Name+' '+$r+','+$c)}
                }
            }
        }
        $items+=$obj
    }
    @{slides=$d.Slides.Count;objects=$items;sourceSnapshot=$snapshot;sourceSha256=$sourceHash}|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $path -Encoding UTF8
}
$paper=@(
    ,@('Original','2.50e-02','4.95e-03','10.7','6.06')
    ,@('No-LLM','1.14e-02','2.48e-03','3.65e-02','1.62e-01')
    ,@('No-RL','1.69e-02','3.18e-03','1.21','2.79')
    ,@('Proposed','8.22e-03','2.08e-03','2.60e-03','1.38e-02')
    ,@('Pure-RL','2.06e-02','7.09e-03','3.24','5.31')
    ,@('No-PT','1.02e-02','2.28e-03','1.26','1.53')
    ,@('No-Reflexion','8.60e-03','1.97e-03','6.08e-03','2.42e-02')
    ,@('MISOCP','2.73e-03','6.54e-04','0.00','0.00')
)
$replica=@()
$csvPath=Join-Path (Split-Path $Source -Parent) 'results\paper-style-v2\table3_execution.csv'
foreach($row in (Import-Csv -LiteralPath $csvPath)){
    $replica+=,@($row.method,('{0:0.00e+00}' -f [double]$row.voltage_deviation_mean),('{0:0.00e+00}' -f [double]$row.voltage_deviation_std),('{0:F2}' -f [double]$row.violation_rate_mean_pct),('{0:F2}' -f [double]$row.violation_rate_std_pct))
}
if($replica.Count -ne 8){throw 'Expected eight execution methods.'}
$app=New-Object -ComObject PowerPoint.Application
foreach($page in 19,24){
    $draft=Join-Path $BuildDir ('page-'+$page+'-draft.pptx')
    Copy-Item -LiteralPath $snapshot -Destination $draft -Force
    $d=$app.Presentations.Open($draft,0,0,0)
    try{
        for($i=$d.Slides.Count;$i -ge 1;$i--){if($i -ne $page){$d.Slides.Item($i).Delete()}}
        $d.PageSetup.FirstSlideNumber=$page;$s=$d.Slides.Item(1)
        if($page -eq 19){
            for($i=$s.Shapes.Count;$i -ge 1;$i--){if($s.Shapes.Item($i).Name -notin @('Group 2','Rectangle 1','调度方向标题')){$s.Shapes.Item($i).Delete()}}
            $s.Shapes.Item('调度方向标题').TextFrame.TextRange.Text='3. 执行性能对比'
            $null=Text $s '表格标题-原论文' '原论文（表 III）' 49 119 397 23 14 $true
            $null=EvidenceTable $s '原论文执行性能表' $paper 144
            $null=Text $s '表格标题-本复现' '本复现（48 个测试日，3 个种子）' 49 316 397 23 14 $true
            $null=EvidenceTable $s '复现执行性能表' $replica 341
            $heads=@('协同控制优于单一资源','预训练与 Reflexion 的作用','MISOCP 提供优化参照')
            $bodies=@(
                "LLM 调度机械设备，RL 调节逆变器无功。`rProposed 的偏差与越限率均低于`rNo-LLM 和 No-RL。",
                "去掉预训练或 Reflexion 后，性能下降。`rProposed 在学习方法中表现最好，`r复现结果呈现相同趋势。",
                "两组结果中，MISOCP 的电压偏差更低，`r越限率均为零。与精确模型下的优化`r参照相比，协作策略仍有提升空间。"
            )
            for($i=0;$i -lt 3;$i++){
                $y=146+116*$i
                $null=Text $s ('说明标题'+$i) $heads[$i] 487 $y 426 28 19 $true
                $null=Text $s ('说明正文'+$i) $bodies[$i] 487 ($y+35) 426 72 17
            }
            $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text="来源：《Two-Stage Active Distribution Network Voltage Control via LLM-RL Collaboration》表III、IV.C执行性能分析，中文原文rc-000187–rc-000208。本复现表：results/paper-style-v2/table3_execution.csv，均值和样本标准差按144个测试日回合统计，MISOCP为48日。原文为3种子×25回合。指标越低越好。Reflexion仅用于离线知识库更新，在线冻结知识库与RL权重。MISOCP原文采用精确网络模型与预测作为优化参照，不将复现求解结果表述为原始非凸联合问题的全局最优。"
        }else{
            $null=Text $s '正文标题' '总结与展望' 56.25 76.45 840 34 22 $true
            $heads=@('1. 两阶段协作控制','2. 策略改进与智能体协调','3. 实验结论','4. 未来方向')
            $bodies=@(
                "日前 LLM 根据预测与历史经验调度 OLTC 和 SCs，`r日内 RL 根据实时量测调节光伏逆变器无功。",
                "LLM 通过 Reflexion 与知识库更新改进策略，`rRL 通过预训练与微调适应日前计划。",
                "协作控制降低电压偏差与越限率，`r完整框架在所比较的学习方法中表现最好。",
                "LLM 擅长知识推理，精确数值计算仍需领域模型支持。`r未来可进一步深化 LLM 与领域专用模型的集成。"
            )
            for($i=0;$i -lt 4;$i++){
                $y=132+96*$i
                $null=Text $s ('正文小标题'+$i) $heads[$i] 56.25 $y 843 28 19 $true
                $null=Text $s ('正文内容'+$i) $bodies[$i] 56.25 ($y+33) 843 52 18
            }
            $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='来源：《Two-Stage Active Distribution Network Voltage Control via LLM-RL Collaboration》V结论，中文原文rc-000224、rc-000229。两阶段分工、Reflexion知识库改进、RL预训练与微调均来自结论段落。学习方法间的性能比较依据表III与IV.C–IV.D。未来方向保留原文对LLM知识推理优势、精确数值计算不足，以及与领域专用模型深层集成的论述。'
        }
        $d.Save();$s.Export((Join-Path $BuildDir ('page-'+$page+'.png')),'PNG',1600,900)
        Audit $d (Join-Path $BuildDir ('verification-'+$page+'.json'))
        Write-Output $draft
    }finally{$d.Close()}
}
$afterHash=SharedHash $Source
@{mainSha256Before=$sourceHash;mainSha256After=$afterHash;mainUnchanged=($sourceHash -eq $afterHash)}|ConvertTo-Json|Set-Content -LiteralPath (Join-Path $BuildDir 'source-check.json') -Encoding UTF8
