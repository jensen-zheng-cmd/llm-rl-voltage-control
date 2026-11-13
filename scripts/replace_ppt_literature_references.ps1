param(
    [string]$Source='',
    [string]$Paper='D:\obsidian_save\论文库\Two-Stage Active Distribution Network Vo\zh-Two-Stage Active Distribution Network Vo.md',
    [string]$BuildDir='D:\Codex\work\llm-rl-ppt-references-20261113'
)
$ErrorActionPreference='Stop'
if(!$Source){$Source=Join-Path $PSScriptRoot '..\LLM-RL.pptx'}
$Source=(Resolve-Path -LiteralPath $Source).Path
New-Item -ItemType Directory -Path $BuildDir -Force|Out-Null
$backup=Join-Path $BuildDir 'before-references.pptx'
if(!(Test-Path -LiteralPath $backup)){Copy-Item -LiteralPath $Source -Destination $backup}
$draft=Join-Path $BuildDir 'references-draft.pptx';Copy-Item -LiteralPath $backup -Destination $draft -Force
$refs=@{}
foreach($line in Get-Content -LiteralPath $Paper -Encoding UTF8){
    if($line -match '^\[(19|20|21|22|23|24)\] '){
        $refs[[string]$Matches[1]]=($line -replace '\s+\^rc-\d+\s*$','')
    }
}
if($refs.Count -ne 6){throw 'Expected original bibliography entries 19–24.'}
$purple=104+256*11+65536*122
function AddText($slide,$name,$value,[double]$x,[double]$y,[double]$w,[double]$h,[double]$size,$font,[int]$color,[bool]$bold=$false){
    $s=$slide.Shapes.AddTextbox(1,$x,$y,$w,$h);$s.Name=$name
    $s.TextFrame.MarginLeft=0;$s.TextFrame.MarginRight=0;$s.TextFrame.MarginTop=0;$s.TextFrame.MarginBottom=0
    $s.TextFrame.AutoSize=0;$s.TextFrame.WordWrap=-1
    $r=$s.TextFrame.TextRange;$r.Text=$value;$r.Font.Name=$font;$r.Font.NameFarEast='Microsoft YaHei'
    $r.Font.Size=$size;$r.Font.Color.RGB=$color;$r.Font.Bold=0
    if($bold){$r.Font.Bold=-1}
    $r.ParagraphFormat.SpaceBefore=0;$r.ParagraphFormat.SpaceAfter=0
    $s.TextFrame2.AutoSize=0;$s.Width=$w;$s.Height=$h
    return $s
}
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open($draft,0,0,0)
try{
    $before=Join-Path $BuildDir 'before';New-Item -ItemType Directory -Path $before -Force|Out-Null
    foreach($slide in $d.Slides){$slide.Export((Join-Path $before ($slide.SlideIndex.ToString()+'.png')),'PNG',1600,900)}
    $s=$d.Slides.Item(7)
    if($s.Shapes.Item('Rectangle 1').TextFrame.TextRange.Text -notmatch 'Literature Review'){throw 'Slide 7 is no longer the literature page.'}
    for($i=$s.Shapes.Count;$i -ge 1;$i--){if($s.Shapes.Item($i).Name -ne 'Rectangle 1' -and $s.Shapes.Item($i).Top -gt 53){$s.Shapes.Item($i).Delete()}}
    $bar=$s.Shapes.AddShape(1,25,71,9,31);$bar.Name='标题强调条';$bar.Fill.ForeColor.RGB=$purple;$bar.Line.Visible=0
    $null=AddText $s '内容标题' '相关研究' 46 67 880 43 28 'Microsoft YaHei' $purple $true
    $y=126.0
    foreach($group in @(@{title='电力系统调度';ids=@(19,20,21)},@{title='LLM 辅助 RL';ids=@(22,23,24)})){
        $null=AddText $s ('分类-'+$group.title) $group.title 40 $y 880 30 21 'Microsoft YaHei' $purple $true
        $y+=35
        foreach($id in $group.ids){
            $shape=AddText $s ('完整参考文献-'+$id) $refs[[string]$id] 40 $y 880 70 16 'Times New Roman' 2500134
            $needed=[Math]::Ceiling($shape.TextFrame.TextRange.BoundHeight)+3
            $shape.Height=$needed
            # The article title is emphasized without omitting any bibliographic fields.
            $tr=$shape.TextFrame.TextRange;$first=$tr.Text.IndexOf('"');$last=$tr.Text.LastIndexOf('"')
            if($first -ge 0 -and $last -gt $first){$tr.Characters($first+2,$last-$first-1).Font.Bold=-1}
            $needed=[Math]::Ceiling($shape.TextFrame.TextRange.BoundHeight)+3;$shape.Height=$needed
            $y+=$needed+7
        }
        $y+=7
    }
    if($y -gt 525){throw ('Reference layout exceeds available body: '+$y)}
    $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text='六条文献按目标论文引言分为电力系统调度与LLM辅助RL两类。书目逐字提取自目标论文参考文献[19]–[24]，保留原文作者写法、完整文章标题、期刊、卷期、页码及年月。来源：'+$Paper
    $d.Save()
    $after=Join-Path $BuildDir 'after';New-Item -ItemType Directory -Path $after -Force|Out-Null
    foreach($slide in $d.Slides){$slide.Export((Join-Path $after ($slide.SlideIndex.ToString()+'.png')),'PNG',1600,900)}
    $audit=@()
    foreach($sh in $s.Shapes){if($sh.HasTextFrame -eq -1 -and $sh.TextFrame.HasText -eq -1){$audit+=@{name=$sh.Name;text=$sh.TextFrame.TextRange.Text;width=$sh.Width;height=$sh.Height;boundWidth=$sh.TextFrame.TextRange.BoundWidth;boundHeight=$sh.TextFrame.TextRange.BoundHeight}}}
    @{slides=$d.Slides.Count;references=$refs;textObjects=$audit;bodyEnd=$y}|ConvertTo-Json -Depth 7|Set-Content -LiteralPath (Join-Path $BuildDir 'verification.json') -Encoding UTF8
    Write-Output ('Draft '+$draft+'; bodyEnd='+$y+'; slides='+$d.Slides.Count)
}finally{$d.Close()}
