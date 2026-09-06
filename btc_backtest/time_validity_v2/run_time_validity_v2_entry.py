from pathlib import Path

src_path = Path(__file__).with_name('run_time_validity_v2.py')
src = src_path.read_text(encoding='utf-8')

# pandas DataFrame has a .rolling() method; the research result column is also
# named 'rolling'. Use explicit column indexing at runtime so pandas 3.x does
# not resolve the method instead of the boolean Series.
src = src.replace("x=x[x.rolling&x.triggered]", "x=x[x['rolling']&x.triggered]")
src = src.replace("x=x[x.rolling&x.triggered&(x.delay<=lim)&(x.R>0)]", "x=x[x['rolling']&x.triggered&(x.delay<=lim)&(x.R>0)]")
src = src.replace("b_1=oo[oo.rolling]", "b_1=oo[oo['rolling']]")
src = src.replace("t2=tr[tr.rolling&tr.triggered&(tr.delay<=lim)].copy()", "t2=tr[tr['rolling']&tr.triggered&(tr.delay<=lim)].copy()")

exec(compile(src, str(src_path), 'exec'), {'__name__': '__main__', '__file__': str(src_path)})
