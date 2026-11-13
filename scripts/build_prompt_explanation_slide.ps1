param(
    [string]$Template='D:\Codex\work\llm-rl-ppt-references-20261113\references-draft.pptx',
    [string]$BuildDir='D:\Codex\work\llm-rl-ppt-prompt-design-20261113'
)
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$draft=Join-Path $BuildDir 'prompt-explanation-draft.pptx'
Copy-Item -LiteralPath $Template -Destination $draft -Force
$purple=104+256*11+65536*122
function Text($slide,$name,$content,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size=16.5,[bool]$bold=$false){
    $s=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$s.Name=$name
    $s.TextFrame.MarginLeft=0;$s.TextFrame.MarginRight=0;$s.TextFrame.MarginTop=0;$s.TextFrame.MarginBottom=0
    $s.TextFrame.AutoSize=0;$s.TextFrame.WordWrap=-1
    $t=$s.TextFrame.TextRange;$t.Text=$content
    $t.Font.Name='Microsoft YaHei';$t.Font.NameFarEast='Microsoft YaHei';$t.Font.Size=$size
    $t.Font.Bold=0;$t.Font.Color.RGB=2500134
    if($bold){$t.Font.Bold=-1;$t.Font.Color.RGB=$purple}
    $t.ParagraphFormat.SpaceBefore=0;$t.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0;$s.Width=$w;$s.Height=$h
    return $s
}
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    # Read from a closed template snapshot, never from the user's active main deck.
    for($i=$d.Slides.Count;$i -ge 1;$i--){if($i -ne 12){$d.Slides.Item($i).Delete()}}
    $d.PageSetup.FirstSlideNumber=12
    $s=$d.Slides.Item(1)
    if($s.Shapes.Item('Picture 6').Type -ne 13){throw 'Original left diagram not found.'}
    for($i=$s.Shapes.Count;$i -ge 1;$i--){
        if($s.Shapes.Item($i).Name -notin @('Group 2','Rectangle 1','调度方向标题','Picture 6')){$s.Shapes.Item($i).Delete()}
    }
    $null=$s.Shapes.Item('调度方向标题').TextFrame.TextRange.Replace('3. 日前LLM智能体','3. 日前LLM智能体-提示词构建')
    $s.Shapes.Item('调度方向标题').Width=840
    $titles=@('1. 环境与任务描述','2. 输出格式','3. 思维链（CoT）引导','4. 少样本示例（知识嵌入）')
    $body=@(
        "系统与设备信息、运行规程及逐小时预测；`r明确日前 OLTC / SC 调度任务。",
        "按标签与 Python 列表输出，提取并校验计划；`r不合规时反馈错误，重新生成。",
        "分析净负荷趋势与幅度，确定 SC 投切和`rOLTC 调节时刻与档位，再按格式输出。",
        "检索相似日的预测、动作、奖励与电压曲线；`r嵌入提示词，通过经验库更新改进决策。"
    )
    for($i=0;$i -lt 4;$i++){
        $y=129+96*$i
        $null=Text $s ('说明标题'+($i+1)) $titles[$i] 491.5 $y 430 31 19 $true
        $null=Text $s ('说明正文'+($i+1)) $body[$i] 491.5 ($y+40) 430 50 16.5
    }
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='保留用户指定的原文提示词结构图。右侧依次概括环境与任务、输出格式、CoT引导、少样本知识嵌入。对应原论文III.A的提示词设计rc-000089–rc-000095。少样本例子包含预测、设备动作、总奖励与电压曲线，知识库离线迭代更新；此页只保留条目及精简说明。独立待插入文件，未打开或修改主PPT。'
    $d.Save()
    $s.Export((Join-Path $BuildDir 'prompt-explanation.png'),'PNG',1600,900)
    $audit=@()
    foreach($sh in $s.Shapes){
        $r=@{name=$sh.Name;type=$sh.Type;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height}
        if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){$r.text=$sh.TextFrame.TextRange.Text;$r.boundWidth=$sh.TextFrame.TextRange.BoundWidth;$r.boundHeight=$sh.TextFrame.TextRange.BoundHeight}
        $audit+=$r
    }
    @{slides=$d.Slides.Count;objects=$audit;mainPptOpened=$false;template=$Template}|ConvertTo-Json -Depth 7|Set-Content -LiteralPath (Join-Path $BuildDir 'verification.json') -Encoding UTF8
    Write-Output ('Draft '+$draft)
}finally{$d.Close()}
