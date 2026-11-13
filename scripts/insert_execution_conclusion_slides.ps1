param(
    [string]$Source='D:\Download\A_storage\新海风\复现：LLM-RL调度论文\LLM-RL.pptx',
    [string]$BuildDir='D:\Codex\work\llm-rl-ppt-insert19-24-20261113'
)
$ErrorActionPreference='Stop'
$root=Split-Path $Source
$snapshot=Join-Path $BuildDir 'before-insertion.pptx'
if(!(Test-Path -LiteralPath $snapshot)){throw 'Capture current source before authoring.'}
$sourceHash=(Get-FileHash -LiteralPath $Source).Hash
$app=New-Object -ComObject PowerPoint.Application
$stage24=Join-Path $BuildDir 'conclusion-centered.pptx'
Copy-Item -LiteralPath (Join-Path $root '第24页-总结与展望-待插入.pptx') -Destination $stage24 -Force
$d=$app.Presentations.Open($stage24,0,0,0)
try{
    $s=$d.Slides.Item(1)
    foreach($sh in $s.Shapes){
        if($sh.Name -match '^正文(小标题|内容)'){
            $sh.Left=135;$sh.Width=690
            $sh.TextFrame.TextRange.ParagraphFormat.Alignment=2
            $sh.TextFrame2.AutoSize=0
            if($sh.TextFrame.TextRange.BoundHeight -gt $sh.Height+0.5){throw 'Conclusion text overflow.'}
        }
    }
    $d.Save();$s.Export((Join-Path $BuildDir 'conclusion-centered.png'),'PNG',1600,900)
}finally{$d.Close()}
$before=Join-Path $BuildDir 'before'
$after=Join-Path $BuildDir 'after'
New-Item -ItemType Directory -Path $before,$after -Force|Out-Null
$d=$app.Presentations.Open($snapshot,-1,0,0)
try{
    $slideCount=$d.Slides.Count
    if($slideCount -ne 25){throw 'Inspect changed source slide order.'}
    $ids=@()
    foreach($s in $d.Slides){$ids+=$s.SlideID;$s.Export((Join-Path $before ($s.SlideIndex.ToString()+'.png')),'PNG',1600,900)}
}finally{$d.Close()}
$draft=Join-Path $BuildDir 'integrated-draft.pptx'
Copy-Item -LiteralPath $snapshot -Destination $draft -Force
$d=$app.Presentations.Open($draft,0,0,0)
try{
    foreach($n in 19,24){
        $old=$d.Slides.Item($n)
        if($old.SlideID -ne $ids[$n-1]){throw 'Unexpected page ordering.'}
        $stage=if($n -eq 19){Join-Path $root '第19页-执行性能对比-待插入.pptx'}else{$stage24}
        if($d.Slides.InsertFromFile($stage,$n-1,1,1) -ne 1){throw 'Slide import failed.'}
        $old.Delete()
    }
    if($d.Slides.Count -ne $slideCount){throw 'Slide count changed.'}
    $d.Save()
    $audit=@()
    foreach($s in $d.Slides){
        $s.Export((Join-Path $after ($s.SlideIndex.ToString()+'.png')),'PNG',1600,900)
        $objects=@()
        if($s.SlideIndex -in 19,24){
            foreach($sh in $s.Shapes){
                if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){
                    $objects+=@{name=$sh.Name;text=$sh.TextFrame.TextRange.Text;left=$sh.Left;top=$sh.Top;width=$sh.Width;height=$sh.Height;boundWidth=$sh.TextFrame.TextRange.BoundWidth;boundHeight=$sh.TextFrame.TextRange.BoundHeight}
                }
            }
        }
        $audit+=@{page=$s.SlideIndex;id=$s.SlideID;objects=$objects}
    }
    @{originalIds=$ids;slides=$d.Slides.Count;pages=$audit;sourceSha256=$sourceHash;source=$Source}|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $BuildDir 'verification.json') -Encoding UTF8
    Write-Output $draft
}finally{$d.Close()}
