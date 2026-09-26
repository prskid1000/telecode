(function(){var e=Uint8Array,t=Uint16Array,n=Int32Array,r=new e([0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0,0,0,0]),i=new e([0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13,0,0]),a=new e([16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15]),o=function(e,r){for(var i=new t(31),a=0;a<31;++a)i[a]=r+=1<<e[a-1];for(var o=new n(i[30]),a=1;a<30;++a)for(var s=i[a];s<i[a+1];++s)o[s]=s-i[a]<<5|a;return{b:i,r:o}},s=o(r,2),c=s.b,l=s.r;c[28]=258,l[258]=28;var u=o(i,0),d=u.b;u.r;for(var f=new t(32768),p=0;p<32768;++p){var m=(p&43690)>>1|(p&21845)<<1;m=(m&52428)>>2|(m&13107)<<2,m=(m&61680)>>4|(m&3855)<<4,f[p]=((m&65280)>>8|(m&255)<<8)>>1}for(var h=(function(e,n,r){for(var i=e.length,a=0,o=new t(n);a<i;++a)e[a]&&++o[e[a]-1];var s=new t(n);for(a=1;a<n;++a)s[a]=s[a-1]+o[a-1]<<1;var c;if(r){c=new t(1<<n);var l=15-n;for(a=0;a<i;++a)if(e[a])for(var u=a<<4|e[a],d=n-e[a],p=s[e[a]-1]++<<d,m=p|(1<<d)-1;p<=m;++p)c[f[p]>>l]=u}else for(c=new t(i),a=0;a<i;++a)e[a]&&(c[a]=f[s[e[a]-1]++]>>15-e[a]);return c}),g=new e(288),p=0;p<144;++p)g[p]=8;for(var p=144;p<256;++p)g[p]=9;for(var p=256;p<280;++p)g[p]=7;for(var p=280;p<288;++p)g[p]=8;for(var _=new e(32),p=0;p<32;++p)_[p]=5;var v=h(g,9,1),y=h(_,5,1),b=function(e){for(var t=e[0],n=1;n<e.length;++n)e[n]>t&&(t=e[n]);return t},x=function(e,t,n){var r=t/8|0;return(e[r]|e[r+1]<<8)>>(t&7)&n},S=function(e,t){var n=t/8|0;return(e[n]|e[n+1]<<8|e[n+2]<<16)>>(t&7)},C=function(e){return(e+7)/8|0},w=function(t,n,r){return(n==null||n<0)&&(n=0),(r==null||r>t.length)&&(r=t.length),new e(t.subarray(n,r))},T=[`unexpected EOF`,`invalid block type`,`invalid length/literal`,`invalid distance`,`stream finished`,`no stream handler`,,`no callback`,`invalid UTF-8 data`,`extra field too long`,`date not in range 1980-2099`,`filename too long`,`stream finishing`,`invalid zip data`],E=function(e,t,n){var r=Error(t||T[e]);if(r.code=e,Error.captureStackTrace&&Error.captureStackTrace(r,E),!n)throw r;return r},D=function(t,n,o,s){var l=t.length,u=s?s.length:0;if(!l||n.f&&!n.l)return o||new e(0);var f=!o,p=f||n.i!=2,m=n.i;f&&(o=new e(l*3));var g=function(t){var n=o.length;if(t>n){var r=new e(Math.max(n*2,t));r.set(o),o=r}},_=n.f||0,T=n.p||0,D=n.b||0,O=n.l,k=n.d,A=n.m,j=n.n,M=l*8;do{if(!O){_=x(t,T,1);var N=x(t,T+1,3);if(T+=3,!N){var P=C(T)+4,ee=t[P-4]|t[P-3]<<8,te=P+ee;if(te>l){m&&E(0);break}p&&g(D+ee),o.set(t.subarray(P,te),D),n.b=D+=ee,n.p=T=te*8,n.f=_;continue}else if(N==1)O=v,k=y,A=9,j=5;else if(N==2){var ne=x(t,T,31)+257,re=x(t,T+10,15)+4,ie=ne+x(t,T+5,31)+1;T+=14;for(var ae=new e(ie),F=new e(19),I=0;I<re;++I)F[a[I]]=x(t,T+I*3,7);T+=re*3;for(var oe=b(F),se=(1<<oe)-1,ce=h(F,oe,1),I=0;I<ie;){var le=ce[x(t,T,se)];T+=le&15;var P=le>>4;if(P<16)ae[I++]=P;else{var L=0,R=0;for(P==16?(R=3+x(t,T,3),T+=2,L=ae[I-1]):P==17?(R=3+x(t,T,7),T+=3):P==18&&(R=11+x(t,T,127),T+=7);R--;)ae[I++]=L}}var z=ae.subarray(0,ne),B=ae.subarray(ne);A=b(z),j=b(B),O=h(z,A,1),k=h(B,j,1)}else E(1);if(T>M){m&&E(0);break}}p&&g(D+131072);for(var ue=(1<<A)-1,de=(1<<j)-1,V=T;;V=T){var L=O[S(t,T)&ue],H=L>>4;if(T+=L&15,T>M){m&&E(0);break}if(L||E(2),H<256)o[D++]=H;else if(H==256){V=T,O=null;break}else{var fe=H-254;if(H>264){var I=H-257,pe=r[I];fe=x(t,T,(1<<pe)-1)+c[I],T+=pe}var me=k[S(t,T)&de],he=me>>4;me||E(3),T+=me&15;var B=d[he];if(he>3){var pe=i[he];B+=S(t,T)&(1<<pe)-1,T+=pe}if(T>M){m&&E(0);break}p&&g(D+131072);var U=D+fe;if(D<B){var ge=u-B,_e=Math.min(B,U);for(ge+D<0&&E(3);D<_e;++D)o[D]=s[ge+D]}for(;D<U;++D)o[D]=o[D-B]}}n.l=O,n.p=V,n.b=D,n.f=_,O&&(_=1,n.m=A,n.d=k,n.n=j)}while(!_);return D!=o.length&&f?w(o,0,D):o.subarray(0,D)},O=new e(0),k=function(e,t){return e[t]|e[t+1]<<8},A=function(e,t){return(e[t]|e[t+1]<<8|e[t+2]<<16|e[t+3]<<24)>>>0},j=function(e,t){return A(e,t)+A(e,t+4)*4294967296};function M(e,t){return D(e,{i:2},t&&t.out,t&&t.dictionary)}var N=typeof TextDecoder<`u`&&new TextDecoder;try{N.decode(O,{stream:!0})}catch{}var P=function(e){for(var t=``,n=0;;){var r=e[n++],i=(r>127)+(r>223)+(r>239);if(n+i>e.length)return{s:t,r:w(e,n-1)};i?i==3?(r=((r&15)<<18|(e[n++]&63)<<12|(e[n++]&63)<<6|e[n++]&63)-65536,t+=String.fromCharCode(55296|r>>10,56320|r&1023)):i&1?t+=String.fromCharCode((r&31)<<6|e[n++]&63):t+=String.fromCharCode((r&15)<<12|(e[n++]&63)<<6|e[n++]&63):t+=String.fromCharCode(r)}};function ee(e,t){if(t){for(var n=``,r=0;r<e.length;r+=16384)n+=String.fromCharCode.apply(null,e.subarray(r,r+16384));return n}else if(N)return N.decode(e);else{var i=P(e),a=i.s,n=i.r;return n.length&&E(8),a}}var te=function(e,t){return t+30+k(e,t+26)+k(e,t+28)},ne=function(e,t,n){var r=k(e,t+28),i=k(e,t+30),a=ee(e.subarray(t+46,t+46+r),!(k(e,t+8)&2048)),o=t+46+r,s=re(e,o,i,n,A(e,t+20),A(e,t+24),A(e,t+42)),c=s[0],l=s[1],u=s[2];return[k(e,t+10),c,l,a,o+i+k(e,t+32),u]},re=function(e,t,n,r,i,a,o){var s=i==4294967295,c=a==4294967295,l=o==4294967295,u=t+n,d=s+c+l;if(r&&d){for(;t+4<u;t+=4+k(e,t+2))if(k(e,t)==1)return[s?j(e,t+4+8*c):i,c?j(e,t+4):a,l?j(e,t+4+8*(c+s)):o,1];r<2&&E(13)}return[i,a,o,0]};function ie(t,n){for(var r={},i=t.length-22;A(t,i)!=101010256;--i)(!i||t.length-i>65558)&&E(13);var a=k(t,i+8);if(!a)return{};var o=A(t,i+16),s=A(t,i-20)==117853008;if(s){var c=A(t,i-12);s=A(t,c)==101075792,s&&(a=A(t,c+32),o=A(t,c+48))}for(var l=n&&n.filter,u=0;u<a;++u){var d=ne(t,o,s),f=d[0],p=d[1],m=d[2],h=d[3],g=d[4],_=d[5],v=te(t,_);o=g,(!l||l({name:h,size:p,originalSize:m,compression:f}))&&(f?f==8?r[h]=M(t.subarray(v,v+p),{out:new e(m)}):E(14,`unknown compression type `+f):r[h]=w(t,v,v+p))}return r}var ae=ArrayBuffer,F=Uint8Array,I=Uint16Array,oe=Int16Array,se=Int32Array,ce=function(e,t,n){if(F.prototype.slice)return F.prototype.slice.call(e,t,n);(t==null||t<0)&&(t=0),(n==null||n>e.length)&&(n=e.length);var r=new F(n-t);return r.set(e.subarray(t,n)),r},le=function(e,t,n,r){if(F.prototype.fill)return F.prototype.fill.call(e,t,n,r);for((n==null||n<0)&&(n=0),(r==null||r>e.length)&&(r=e.length);n<r;++n)e[n]=t;return e},L=function(e,t,n,r){if(F.prototype.copyWithin)return F.prototype.copyWithin.call(e,t,n,r);for((n==null||n<0)&&(n=0),(r==null||r>e.length)&&(r=e.length);n<r;)e[t++]=e[n++]},R=[`invalid zstd data`,`window size too large (>2046MB)`,`invalid block type`,`FSE accuracy too high`,`match distance too far back`,`unexpected EOF`],z=function(e,t,n){var r=Error(t||R[e]);if(r.code=e,Error.captureStackTrace&&Error.captureStackTrace(r,z),!n)throw r;return r},B=function(e,t,n){for(var r=0,i=0;r<n;++r)i|=e[t++]<<(r<<3);return i},ue=function(e,t){return(e[t]|e[t+1]<<8|e[t+2]<<16|e[t+3]<<24)>>>0},de=function(e,t){var n=e[0]|e[1]<<8|e[2]<<16;if(n==3126568&&e[3]==253){var r=e[4],i=r>>5&1,a=r>>2&1,o=r&3,s=r>>6;r&8&&z(0);var c=6-i,l=o==3?4:o,u=B(e,c,l);c+=l;var d=s?1<<s:i,f=B(e,c,d)+(s==1&&256),p=f;if(!i){var m=1<<10+(e[5]>>3);p=m+(m>>3)*(e[5]&7)}p>2145386496&&z(1);var h=new F((t==1?f||p:t?0:p)+12);return h[0]=1,h[4]=4,h[8]=8,{b:c+d,y:0,l:0,d:u,w:t&&t!=1?t:h.subarray(12),e:p,o:new se(h.buffer,0,3),u:f,c:a,m:Math.min(131072,p)}}else if((n>>4|e[3]<<20)==25481893)return ue(e,4)+8;z(0)},V=function(e){for(var t=0;1<<t<=e;++t);return t-1},H=function(e,t,n){var r=(t<<3)+4,i=(e[t]&15)+5;i>n&&z(3);for(var a=1<<i,o=a,s=-1,c=-1,l=-1,u=a,d=new ae(512+(a<<2)),f=new oe(d,0,256),p=new I(d,0,256),m=new I(d,512,a),h=512+(a<<1),g=new F(d,h,a),_=new F(d,h+a);s<255&&o>0;){var v=V(o+1),y=r>>3,b=(1<<v+1)-1,x=(e[y]|e[y+1]<<8|e[y+2]<<16)>>(r&7)&b,S=(1<<v)-1,C=b-o-1,w=x&S;if(w<C?(r+=v,x=w):(r+=v+1,x>S&&(x-=C)),f[++s]=--x,x==-1?(o+=x,g[--u]=s):o-=x,!x)do{var T=r>>3;c=(e[T]|e[T+1]<<8)>>(r&7)&3,r+=2,s+=c}while(c==3)}(s>255||o)&&z(0);for(var E=0,D=(a>>1)+(a>>3)+3,O=a-1,k=0;k<=s;++k){var A=f[k];if(A<1){p[k]=-A;continue}for(l=0;l<A;++l){g[E]=k;do E=E+D&O;while(E>=u)}}for(E&&z(0),l=0;l<a;++l){var j=p[g[l]]++;m[l]=(j<<(_[l]=i-V(j)))-a}return[r+7>>3,{b:i,s:g,n:_,t:m}]},fe=function(e,t){var n=0,r=-1,i=new F(292),a=e[t],o=i.subarray(0,256),s=i.subarray(256,268),c=new I(i.buffer,268);if(a<128){var l=H(e,t+1,6),u=l[0],d=l[1];t+=a;var f=u<<3,p=e[t];p||z(0);for(var m=0,h=0,g=d.b,_=g,v=(++t<<3)-8+V(p);v-=g,!(v<f);){var y=v>>3;if(m+=(e[y]|e[y+1]<<8)>>(v&7)&(1<<g)-1,o[++r]=d.s[m],v-=_,v<f)break;y=v>>3,h+=(e[y]|e[y+1]<<8)>>(v&7)&(1<<_)-1,o[++r]=d.s[h],g=d.n[m],m=d.t[m],_=d.n[h],h=d.t[h]}++r>255&&z(0)}else{for(r=a-127;n<r;n+=2){var b=e[++t];o[n]=b>>4,o[n+1]=b&15}++t}var x=0;for(n=0;n<r;++n){var S=o[n];S>11&&z(0),x+=S&&1<<S-1}var C=V(x)+1,w=1<<C,T=w-x;for(T&T-1&&z(0),o[r++]=V(T)+1,n=0;n<r;++n){var S=o[n];++s[o[n]=S&&C+1-S]}var E=new F(w<<1),D=E.subarray(0,w),O=E.subarray(w);for(c[C]=0,n=C;n>0;--n){var k=c[n];le(O,n,k,c[n-1]=k+s[n]*(1<<C-n))}for(c[0]!=w&&z(0),n=0;n<r;++n){var A=o[n];if(A){var j=c[A];le(D,n,j,c[A]=j+(1<<C-A))}}return[t,{n:O,b:C,s:D}]},pe=H(new F([81,16,99,140,49,198,24,99,12,33,196,24,99,102,102,134,70,146,4]),0,6)[1],me=H(new F([33,20,196,24,99,140,33,132,16,66,8,33,132,16,66,8,33,68,68,68,68,68,68,68,68,36,9]),0,6)[1],he=H(new F([32,132,16,66,102,70,68,68,68,68,36,73,2]),0,5)[1],U=function(e,t){for(var n=e.length,r=new se(n),i=0;i<n;++i)r[i]=t,t+=1<<e[i];return r},ge=new F(new se([0,0,0,0,16843009,50528770,134678020,202050057,269422093]).buffer,0,36),_e=U(ge,0),ve=new F(new se([0,0,0,0,0,0,0,0,16843009,50528770,117769220,185207048,252579084,16]).buffer,0,53),ye=U(ve,3),be=function(e,t,n){var r=e.length,i=t.length,a=e[r-1],o=(1<<n.b)-1,s=-n.b;a||z(0);for(var c=0,l=n.b,u=(r<<3)-8+V(a)-l,d=-1;u>s&&d<i;){var f=u>>3,p=(e[f]|e[f+1]<<8|e[f+2]<<16)>>(u&7);c=(c<<l|p)&o,t[++d]=n.s[c],u-=l=n.n[c]}(u!=s||d+1!=i)&&z(0)},xe=function(e,t,n){var r=6,i=t.length+3>>2,a=i<<1,o=i+a;be(e.subarray(r,r+=e[0]|e[1]<<8),t.subarray(0,i),n),be(e.subarray(r,r+=e[2]|e[3]<<8),t.subarray(i,a),n),be(e.subarray(r,r+=e[4]|e[5]<<8),t.subarray(a,o),n),be(e.subarray(r),t.subarray(o),n)},Se=function(e,t,n){var r,i=t.b,a=e[i],o=a>>1&3;t.l=a&1;var s=a>>3|e[i+1]<<5|e[i+2]<<13,c=(i+=3)+s;if(o==1)return i>=e.length?void 0:(t.b=i+1,n?(le(n,e[i],t.y,t.y+=s),n):le(new F(s),e[i]));if(!(c>e.length)){if(o==0)return t.b=c,n?(n.set(e.subarray(i,c),t.y),t.y+=s,n):ce(e,i,c);if(o==2){var l=e[i],u=l&3,d=l>>2&3,f=l>>4,p=0,m=0;u<2?d&1?f|=e[++i]<<4|(d&2&&e[++i]<<12):f=l>>3:(m=d,d<2?(f|=(e[++i]&63)<<4,p=e[i]>>6|e[++i]<<2):d==2?(f|=e[++i]<<4|(e[++i]&3)<<12,p=e[i]>>2|e[++i]<<6):(f|=e[++i]<<4|(e[++i]&63)<<12,p=e[i]>>6|e[++i]<<2|e[++i]<<10)),++i;var h=n?n.subarray(t.y,t.y+t.m):new F(t.m),g=h.length-f;if(u==0)h.set(e.subarray(i,i+=f),g);else if(u==1)le(h,e[i++],g);else{var _=t.h;if(u==2){var v=fe(e,i);p+=i-(i=v[0]),t.h=_=v[1]}else _||z(0);(m?xe:be)(e.subarray(i,i+=p),h.subarray(g),_)}var y=e[i++];if(y){y==255?y=(e[i++]|e[i++]<<8)+32512:y>127&&(y=y-128<<8|e[i++]);var b=e[i++];b&3&&z(0);for(var x=[me,he,pe],S=2;S>-1;--S){var C=b>>(S<<1)+2&3;if(C==1){var w=new F([0,0,e[i++]]);x[S]={s:w.subarray(2,3),n:w.subarray(0,1),t:new I(w.buffer,0,1),b:0}}else C==2?(r=H(e,i,9-(S&1)),i=r[0],x[S]=r[1]):C==3&&(t.t||z(0),x[S]=t.t[S])}var T=t.t=x,E=T[0],D=T[1],O=T[2],k=e[c-1];k||z(0);var A=(c<<3)-8+V(k)-O.b,j=A>>3,M=0,N=(e[j]|e[j+1]<<8)>>(A&7)&(1<<O.b)-1;j=(A-=D.b)>>3;var P=(e[j]|e[j+1]<<8)>>(A&7)&(1<<D.b)-1;j=(A-=E.b)>>3;var ee=(e[j]|e[j+1]<<8)>>(A&7)&(1<<E.b)-1;for(++y;--y;){var te=O.s[N],ne=O.n[N],re=E.s[ee],ie=E.n[ee],ae=D.s[P],oe=D.n[P];j=(A-=ae)>>3;var se=1<<ae,L=se+((e[j]|e[j+1]<<8|e[j+2]<<16|e[j+3]<<24)>>>(A&7)&se-1);j=(A-=ve[re])>>3;var R=ye[re]+((e[j]|e[j+1]<<8|e[j+2]<<16)>>(A&7)&(1<<ve[re])-1);j=(A-=ge[te])>>3;var B=_e[te]+((e[j]|e[j+1]<<8|e[j+2]<<16)>>(A&7)&(1<<ge[te])-1);if(j=(A-=ne)>>3,N=O.t[N]+((e[j]|e[j+1]<<8)>>(A&7)&(1<<ne)-1),j=(A-=ie)>>3,ee=E.t[ee]+((e[j]|e[j+1]<<8)>>(A&7)&(1<<ie)-1),j=(A-=oe)>>3,P=D.t[P]+((e[j]|e[j+1]<<8)>>(A&7)&(1<<oe)-1),L>3)t.o[2]=t.o[1],t.o[1]=t.o[0],t.o[0]=L-=3;else{var ue=L-(B!=0);ue?(L=ue==3?t.o[0]-1:t.o[ue],ue>1&&(t.o[2]=t.o[1]),t.o[1]=t.o[0],t.o[0]=L):L=t.o[0]}for(var S=0;S<B;++S)h[M+S]=h[g+S];M+=B,g+=B;var de=M-L;if(de<0){var U=-de,Se=t.e+de;U>R&&(U=R);for(var S=0;S<U;++S)h[M+S]=t.w[Se+S];M+=U,R-=U,de=0}for(var S=0;S<R;++S)h[M+S]=h[de+S];M+=R}if(M!=g)for(;g<h.length;)h[M++]=h[g++];else M=h.length;n?t.y+=M:h=ce(h,0,M)}else if(n){if(t.y+=f,g)for(var S=0;S<f;++S)h[S]=h[g+S]}else g&&(h=ce(h,g));return t.b=c,h}z(2)}},Ce=function(e,t){if(e.length==1)return e[0];for(var n=new F(t),r=0,i=0;r<e.length;++r){var a=e[r];n.set(a,i),i+=a.length}return n};function we(e,t){for(var n=[],r=+!t,i=0,a=0;e.length;){var o=de(e,r||t);if(typeof o==`object`){for(r?(t=null,o.w.length==o.u&&(n.push(t=o.w),a+=o.u)):(n.push(t),o.e=0);!o.l;){var s=Se(e,o,t);s||z(5),t?o.e=o.y:(n.push(s),a+=s.length,L(o.w,0,s.length),o.w.set(s,o.w.length-s.length))}i=o.b+o.c*4}else i=o;e=e.subarray(i)}return Ce(n,a)}new Uint8Array([40,181,47,253]);function Te(e){return e.length>=4&&e[0]===40&&e[1]===181&&e[2]===47&&e[3]===253}function Ee(e){if(new TextDecoder().decode(e.slice(0,8))!==`fig-kiwi`)return null;let t=new DataView(e.buffer,e.byteOffset,e.byteLength),n=12,r=[];for(;n<e.length;){let i=t.getUint32(n,!0);n+=4,r.push(e.slice(n,n+i)),n+=i}return r.length>=2?r:null}let De=new Int32Array(1),Oe=new Float32Array(De.buffer),ke=new TextDecoder;var Ae=class{_data;_index;length;constructor(e){if(e&&!(e instanceof Uint8Array))throw Error(`Must initialize a ByteBuffer with a Uint8Array`);this._data=e||new Uint8Array(256),this._index=0,this.length=e?e.length:0}get offset(){return this._index}set offset(e){this._index=e}toUint8Array(){return this._data.subarray(0,this.length)}readByte(){return this._data[this._index++]}readByteArray(){let e=this.readVarUint(),t=this._index;return this._index=t+e,this._data.slice(t,t+e)}skipByteArray(){let e=this.readVarUint();this._index+=e}readVarFloat(){let e=this._index,t=this._data,n=t[e];if(n===0)return this._index=e+1,0;let r=n|t[e+1]<<8|t[e+2]<<16|t[e+3]<<24;return this._index=e+4,r=r<<23|r>>>9,De[0]=r,Oe[0]}readVarUint(){let e=this._data,t=this._index,n=e[t++],r=n&127;return n<128||(n=e[t++],r|=(n&127)<<7,n<128)||(n=e[t++],r|=(n&127)<<14,n<128)||(n=e[t++],r|=(n&127)<<21,n<128)?(this._index=t,r):(n=e[t++],r|=(n&127)<<28,this._index=t,r>>>0)}readVarInt(){let e=this.readVarUint()|0;return e&1?~(e>>>1):e>>>1}readVarUint64(){let e=BigInt(0),t=BigInt(0),n=BigInt(7),r=this.readByte();for(;r&128&&t<56;)e|=BigInt(r&127)<<t,t+=n,r=this.readByte();return e|=BigInt(r)<<t,e}readVarInt64(){let e=this.readVarUint64(),t=BigInt(1),n=e&t;return e>>=t,n?~e:e}readString(){let e=this._index,t=this.findStringTerminator(e);return this._index=t+1,ke.decode(this._data.subarray(e,t))}skipString(){this._index=this.findStringTerminator(this._index)+1}findStringTerminator(e){let t=this._data,n=e;for(;n<t.length&&t[n]!==0;)n++;if(n>=t.length)throw Error(`Unterminated string in Kiwi message`);return n}_growBy(e){if(this.length+e>this._data.length){let t=new Uint8Array(this.length+e<<1);t.set(this._data),this._data=t}this.length+=e}writeByte(e){let t=this.length;this._growBy(1),this._data[t]=e}writeByteArray(e){this.writeVarUint(e.length);let t=this.length;this._growBy(e.length),this._data.set(e,t)}writeVarFloat(e){let t=this.length;Oe[0]=e;let n=De[0];if(n=n>>>23|n<<9,!(n&255)){this.writeByte(0);return}this._growBy(4);let r=this._data;r[t]=n,r[t+1]=n>>8,r[t+2]=n>>16,r[t+3]=n>>24}writeVarUint(e){if(e<0||e>4294967295)throw Error(`Outside uint range: `+e);do{let t=e&127;e>>>=7,this.writeByte(e?t|128:t)}while(e)}writeVarInt(e){if(e<-2147483648||e>2147483647)throw Error(`Outside int range: `+e);this.writeVarUint((e<<1^e>>31)>>>0)}writeVarUint64(e){if(typeof e==`string`)e=BigInt(e);else if(typeof e!=`bigint`)throw Error(`Expected bigint but got ${typeof e}: ${String(e)}`);if(e<0||e>BigInt(`0xFFFFFFFFFFFFFFFF`))throw Error(`Outside uint64 range: `+e);let t=BigInt(127),n=BigInt(7);for(let r=0;e>t&&r<8;r++)this.writeByte(Number(e&t)|128),e>>=n;this.writeByte(Number(e))}writeVarInt64(e){if(typeof e==`string`)e=BigInt(e);else if(typeof e!=`bigint`)throw Error(`Expected bigint but got ${typeof e}: ${String(e)}`);if(e<-BigInt(`0x8000000000000000`)||e>BigInt(`0x7FFFFFFFFFFFFFFF`))throw Error(`Outside int64 range: `+e);let t=BigInt(1);this.writeVarUint64(e<0?~(e<<t):e<<t)}writeString(e){let t;for(let n=0;n<e.length;n++){let r=e.charCodeAt(n);if(n+1===e.length||r<55296||r>=56320)t=r;else{let i=e.charCodeAt(++n);t=(r<<10)+i+-56613888}if(t===0)throw Error(`Cannot encode a string containing the null character`);t<128?this.writeByte(t):(t<2048?this.writeByte(t>>6&31|192):(t<65536?this.writeByte(t>>12&15|224):(this.writeByte(t>>18&7|240),this.writeByte(t>>12&63|128)),this.writeByte(t>>6&63|128)),this.writeByte(t&63|128))}this.writeByte(0)}};function W(e){return JSON.stringify(e)}function G(e,t,n){var r=Error(e);throw r.line=t,r.column=n,r}let je=[`bool`,`byte`,`float`,`int`,`int64`,`string`,`uint`,`uint64`],Me=[`ByteBuffer`,`package`],Ne=/((?:-|\b)\d+\b|[=;{}]|\[\]|\[deprecated\]|\b[A-Za-z_][A-Za-z0-9_]*\b|\/\/.*|\s+)/g,Pe=/^[A-Za-z_][A-Za-z0-9_]*$/,Fe=/^\/\/.*|\s+$/,Ie=/^=$/,Le=/^$/,Re=/^;$/,ze=/^-?\d+$/,Be=/^\{$/,Ve=/^\}$/,He=/^\[\]$/,Ue=/^enum$/,We=/^struct$/,Ge=/^message$/,Ke=/^package$/,qe=/^\[deprecated\]$/;function Je(e){let t=e.split(Ne),n=[],r=0,i=0;for(let e=0;e<t.length;e++){let a=t[e];e&1?Fe.test(a)||n.push({text:a,line:i+1,column:r+1}):a!==``&&G(`Syntax error `+W(a),i+1,r+1);let o=a.split(`
`);o.length>1&&(r=0),i+=o.length-1,r+=o[o.length-1].length}return n.push({text:``,line:i,column:r}),n}function Ye(e){function t(){return e[s]}function n(e){return e.test(t().text)?(s++,!0):!1}function r(e,r){if(!n(e)){let e=t();G(`Expected `+r+` but found `+W(e.text),e.line,e.column)}}function i(){let e=t();G(`Unexpected token `+W(e.text),e.line,e.column)}let a=[],o=null,s=0;for(n(Ke)&&(o=t().text,r(Pe,`identifier`),r(Re,`";"`));s<e.length&&!n(Le);){let e=[],o;n(Ue)?o=`ENUM`:n(We)?o=`STRUCT`:n(Ge)?o=`MESSAGE`:i();let s=t();for(r(Pe,`identifier`),r(Be,`"{"`);!n(Ve);){let i=null,a=!1,s=!1;o!==`ENUM`&&(i=t().text,r(Pe,`identifier`),a=n(He));let c=t();r(Pe,`identifier`);let l=null;o!==`STRUCT`&&(r(Ie,`"="`),l=t(),r(ze,`integer`),(l.text|0)+``!==l.text&&G(`Invalid integer `+W(l.text),l.line,l.column));let u=t();n(qe)&&(o!==`MESSAGE`&&G(`Cannot deprecate this field`,u.line,u.column),s=!0),r(Re,`";"`),e.push({name:c.text,line:c.line,column:c.column,type:i,isArray:a,isDeprecated:s,value:l===null?e.length+1:l.text|0})}a.push({name:s.text,line:s.line,column:s.column,kind:o,fields:e})}return{package:o,definitions:a}}function Xe(e){let t=je.slice(),n={};for(let r=0;r<e.definitions.length;r++){let i=e.definitions[r];t.includes(i.name)&&G(`The type `+W(i.name)+` is defined twice`,i.line,i.column),Me.includes(i.name)&&G(`The type name `+W(i.name)+` is reserved`,i.line,i.column),t.push(i.name),n[i.name]=i}for(let n=0;n<e.definitions.length;n++){let r=e.definitions[n],i=r.fields;if(r.kind===`ENUM`||i.length===0)continue;for(let e=0;e<i.length;e++){let n=i[e];t.includes(n.type)||G(`The type `+W(n.type)+` is not defined for field `+W(n.name),n.line,n.column)}let a=[];for(let e=0;e<i.length;e++){let t=i[e];a.includes(t.value)&&G(`The id for field `+W(t.name)+` is used twice`,t.line,t.column),t.value<=0&&G(`The id for field `+W(t.name)+` must be positive`,t.line,t.column),a.push(t.value)}}let r={},i=e=>{let t=n[e];if(t&&t.kind===`STRUCT`&&(r[e]===1&&G(`Recursive nesting of `+W(e)+` is not allowed`,t.line,t.column),r[e]!==2&&t)){r[e]=1;let n=t.fields;for(let e=0;e<n.length;e++){let t=n[e];t.isArray||i(t.type)}r[e]=2}return!0};for(let t=0;t<e.definitions.length;t++)i(e.definitions[t].name)}function Ze(e){let t=Ye(Je(e));return Xe(t),t}function Qe(e){return e}function $e(e){return e}function et(e){return e}function tt(e,t){return Object.hasOwn(e,t)}function nt(e){let t=Object.values(e);for(let n=0;n<t.length;n++){let r=t[n];if(r.kind!==`ENUM`)for(let t=0;t<r.fields.length;t++){let n=r.fields[t],i=n.type;i===null&&G(`Invalid type null for field `+W(n.name),n.line,n.column),!je.includes(i)&&!tt(e,i)&&G(`Invalid type `+W(i)+` for field `+W(n.name),n.line,n.column)}}}function rt(e,t,n,r){switch(n){case`bool`:return!!r.readByte();case`byte`:return r.readByte();case`int`:return r.readVarInt();case`uint`:return r.readVarUint();case`float`:return r.readVarFloat();case`string`:return r.readString();case`int64`:return r.readVarInt64();case`uint64`:return r.readVarUint64();default:{let i=t[n];return i||G(`Invalid type `+W(n),0,0),i.kind===`ENUM`?et(e[i.name])[r.readVarUint()]:Qe(e[`decode`+i.name])(r)}}}function it(e,t,n,r,i){switch(n){case`bool`:case`byte`:i.writeByte(r);return;case`int`:i.writeVarInt(r);return;case`uint`:i.writeVarUint(r);return;case`float`:i.writeVarFloat(r);return;case`string`:i.writeString(r);return;case`int64`:i.writeVarInt64(r);return;case`uint64`:i.writeVarUint64(r);return;default:{let a=t[n];if(a||G(`Invalid type `+W(n),0,0),a.kind===`ENUM`){let t=et(e[a.name])[r];if(t===void 0)throw Error(`Invalid value `+JSON.stringify(r)+` for enum `+W(a.name));i.writeVarUint(t)}else $e(e[`encode`+a.name])(r,i)}}}function at(e,t,n,r,i){let a=n.type;if(a===null&&G(`Invalid type null for field `+W(n.name),n.line,n.column),n.isArray){if(n.isDeprecated){if(a===`byte`)r.readByteArray();else{let n=r.readVarUint();for(;n-->0;)rt(e,t,a,r)}return}if(a===`byte`){i[n.name]=r.readByteArray();return}let o=r.readVarUint(),s=Array.from({length:o});i[n.name]=s;for(let n=0;n<o;n++)s[n]=rt(e,t,a,r);return}if(n.isDeprecated){rt(e,t,a,r);return}i[n.name]=rt(e,t,a,r)}function ot(e,t,n,r,i){let a=n.type;if(a===null&&G(`Invalid type null for field `+W(n.name),n.line,n.column),n.isArray){if(a===`byte`){i.writeByteArray(r);return}let n=r;i.writeVarUint(n.length);for(let r=0;r<n.length;r++)it(e,t,a,n[r],i);return}it(e,t,a,r,i)}function st(e,t,n){let r=new Map;for(let e=0;e<n.fields.length;e++)r.set(n.fields[e].value,n.fields[e]);return function(i){let a=i instanceof e.ByteBuffer?i:new e.ByteBuffer(i),o={};if(n.kind===`MESSAGE`)for(;;){let n=a.readVarUint();if(n===0)return o;let i=r.get(n);if(!i)throw Error(`Attempted to parse invalid message`);at(e,t,i,a,o)}else{for(let r=0;r<n.fields.length;r++)at(e,t,n.fields[r],a,o);return o}}}function ct(e,t,n){return function(r,i){let a=!i,o=i||new e.ByteBuffer;for(let i=0;i<n.fields.length;i++){let a=n.fields[i];if(a.isDeprecated)continue;let s=r[a.name];if(s!=null)n.kind===`MESSAGE`&&o.writeVarUint(a.value),ot(e,t,a,s,o);else if(n.kind===`STRUCT`)throw Error(`Missing required field `+W(a.name))}if(n.kind===`MESSAGE`&&o.writeVarUint(0),a)return o.toUint8Array()}}function lt(e){let t=Object.create(null);for(let n=0;n<e.definitions.length;n++)t[e.definitions[n].name]=e.definitions[n];nt(t);let n={ByteBuffer:Ae};for(let r=0;r<e.definitions.length;r++){let i=e.definitions[r];switch(i.kind){case`ENUM`:{let e={};for(let t=0;t<i.fields.length;t++){let n=i.fields[t];e[n.name]=n.value,e[n.value]=n.name}n[i.name]=e;break}case`STRUCT`:case`MESSAGE`:n[`decode`+i.name]=st(n,t,i),n[`encode`+i.name]=ct(n,t,i);break;default:G(`Invalid definition kind `+W(i.kind),i.line,i.column);break}}return n}let ut=[`bool`,`byte`,`int`,`uint`,`float`,`string`,`int64`,`uint64`],dt=[`ENUM`,`STRUCT`,`MESSAGE`];function ft(e){let t=e instanceof Ae?e:new Ae(e),n=t.readVarUint(),r=[];for(let e=0;e<n;e++){let e=t.readString(),n=t.readByte(),i=t.readVarUint(),a=[];for(let e=0;e<i;e++){let e=t.readString(),r=t.readVarInt(),i=!!(t.readByte()&1),o=t.readVarUint();a.push({name:e,line:0,column:0,type:dt[n]===`ENUM`?null:r,isArray:i,isDeprecated:!1,value:o})}r.push({name:e,line:0,column:0,kind:dt[n],fields:a})}for(let e=0;e<n;e++){let t=r[e].fields;for(let e=0;e<t.length;e++){let n=t[e],i=n.type;if(i!==null&&i<0){if(~i>=ut.length)throw Error(`Invalid type `+i);n.type=ut[~i]}else{if(i!==null&&i>=r.length)throw Error(`Invalid type `+i);n.type=i===null?null:r[i].name}}}return{package:null,definitions:r}}function pt(e){for(let t of e.definitions)mt(t),t.kind===`ENUM`&&ht(t)}function mt(e){let t=new Set;for(let n of e.fields)t.has(n.name)&&G(`The field ${W(n.name)} is defined twice in ${W(e.name)}`,n.line,n.column),t.add(n.name)}function ht(e){let t=new Set;for(let n of e.fields)t.has(n.value)&&G(`The enum value ${n.value} is used twice in ${W(e.name)}`,n.line,n.column),t.add(n.value)}function gt(e){return new Map(e.fields.map(e=>[e.value,e]))}function _t(e,t){let n=e.fields.get(t.name);return n||(n=gt(t),e.fields.set(t.name,n)),n}function vt(e,t,n){switch(t.type){case`bool`:case`byte`:e.readByte();return;case`int`:e.readVarInt();return;case`uint`:e.readVarUint();return;case`float`:e.readVarFloat();return;case`string`:e.skipString();return;case`int64`:e.readVarInt64();return;case`uint64`:e.readVarUint64();return}let r=t.type?n.definitions.get(t.type):void 0;if(!r)throw Error(`Invalid Kiwi field type: ${String(t.type)}`);if(r.kind===`ENUM`){e.readVarUint();return}bt(e,r,n)}function yt(e,t,n){if(!t.isArray){vt(e,t,n);return}if(t.type===`byte`){e.skipByteArray();return}let r=e.readVarUint();for(;r-->0;)vt(e,t,n)}function bt(e,t,n){if(t.kind===`STRUCT`){for(let r of t.fields)yt(e,r,n);return}let r=_t(n,t);for(;;){let i=e.readVarUint();if(i===0)return;let a=r.get(i);if(!a)throw Error(`Invalid field ${i} in Kiwi ${t.name}`);yt(e,a,n)}}function xt(e,t,n){let r=e.readVarUint(),i=t.type?n.definitions.get(t.type):void 0;return i?.kind===`ENUM`?i.fields.find(e=>e.value===r)?.name??null:null}function St(e,t,n){let r=_t(n,t),i=null,a=null,o=null,s=null,c=null,l=null,u=null,d=null,f=!1;for(;;){let t=e.readVarUint();if(t===0)break;let p=r.get(t);if(!p)throw Error(`Invalid field ${t} in Kiwi NodeChange`);switch(p.name){case`guid`:i=e.readVarUint(),a=e.readVarUint();break;case`parentIndex`:o=e.readVarUint(),s=e.readVarUint(),c=e.offset,e.skipString();break;case`phase`:l=xt(e,p,n);break;case`type`:u=xt(e,p,n);break;case`name`:u===`DOCUMENT`||u===`CANVAS`?d=e.readString():e.skipString();break;case`internalOnly`:f=!!e.readByte();break;default:yt(e,p,n)}}if(u!==`DOCUMENT`&&u!==`CANVAS`||i===null||a===null)return null;let p=null;if(c!==null){let t=e.offset;e.offset=c,p=e.readString(),e.offset=t}let m=o===null||s===null?null:`${o}:${s}`;return{sourceId:`${i}:${a}`,parentId:m,position:p,phase:l,type:u,name:d??`Page`,internalOnly:f}}function Ct(e,t){let n=new Map(e.definitions.map(e=>[e.name,e])),r={definitions:n,fields:new Map},i=n.get(`Message`),a=n.get(`NodeChange`);if(i?.kind!==`MESSAGE`||a?.kind!==`MESSAGE`)return[];let o=new Ae(t),s=_t(r,i),c=[];for(;;){let e=o.readVarUint();if(e===0)break;let t=s.get(e);if(!t)throw Error(`Invalid field ${e} in Kiwi Message`);if(t.name!==`nodeChanges`||!t.isArray){yt(o,t,r);continue}let n=o.readVarUint();for(;n-->0;){let e=St(o,a,r);e&&c.push(e)}break}let l=c.find(e=>e.type===`DOCUMENT`&&e.phase!==`REMOVED`)?.sourceId;return c.filter(e=>e.type===`CANVAS`&&e.phase!==`REMOVED`&&(!l||e.parentId===l)).sort((e,t)=>{let n=e.position??``,r=t.position??``;return n<r?-1:+(n>r)}).map(({sourceId:e,name:t,position:n,internalOnly:r})=>({sourceId:e,name:t,position:n,internalOnly:r}))}function wt(e){for(let t of e){if(t.pluginData&&t.pluginData.length>1){let e=new Map;for(let n of t.pluginData)e.set(`${n.pluginID}\0${n.key}\0${n.value}`,n);e.size<t.pluginData.length&&(t.pluginData=[...e.values()])}if(t.pluginRelaunchData&&t.pluginRelaunchData.length>1){let e=new Map;for(let n of t.pluginRelaunchData)e.set(`${n.pluginID}\0${n.command}\0${n.message}\0${n.isDeleted}`,n);e.size<t.pluginRelaunchData.length&&(t.pluginRelaunchData=[...e.values()])}}}function Tt(e){if(new TextDecoder().decode(e.slice(0,8))!==`fig-kiwi`)return null;let t=new DataView(e.buffer,e.byteOffset,e.byteLength),n=t.getUint32(8,!0),r=12,i=[];for(;r<e.length&&!(r+4>e.length);){let n=t.getUint32(r,!0);if(r+=4,r+n>e.length)throw Error(`Corrupted .fig file: chunk at offset ${r-4} declares length ${n} but only ${e.length-r} bytes remain`);i.push(e.slice(r,r+n)),r+=n}if(i.length<2)return null;let a=i[1],o;if(Te(a))o=we(a);else try{o=M(a)}catch{throw Error(`Failed to decompress fig-kiwi data chunk`)}return{schemaDeflated:i[0],dataRaw:o,version:n}}function Et(e,t){let n=Tt(e);if(!n)throw Error(`Invalid fig-kiwi container`);let r=ft(new Ae(M(n.schemaDeflated)));if(t)try{let e=Ct(r,n.dataRaw);e.length>0&&t(e)}catch(e){console.warn(`Failed to scan FIG page manifest; continuing with full decode:`,e)}let i=lt(r).decodeMessage(n.dataRaw),a=i.nodeChanges;if(!a||a.length===0)throw Error(`No nodes found in .fig file`);return wt(a),{nodeChanges:a,blobs:(i.blobs??[]).map(e=>e.bytes instanceof Uint8Array?e.bytes:new Uint8Array(Object.values(e.bytes))),figKiwiVersion:n.version,figSchemaDeflated:n.schemaDeflated}}let Dt=new Uint8Array([137,80,78,71,13,10,26,10]);function Ot(e){return Dt.every((t,n)=>e[n]===t)}function kt(e){let t=e.toLowerCase();return t.endsWith(`.png`)||t.endsWith(`.jpg`)||t.endsWith(`.json`)}function At(e){let t=e[`canvas.fig`]??e.canvas;if(t)return t;let n=null;for(let[t,r]of Object.entries(e))!r||kt(t)||(!n||r.byteLength>n.byteLength)&&(n=r);return n}function jt(e){return e===`canvas.fig`||e===`canvas`}function Mt(e,t){let n=Ee(e);if(!n)return null;let r=Et(e,t),i=n.slice(2).find(Ot)??null;return{...r,images:[],thumbnailPNG:i,metaJSON:null}}function Nt(e,t){let n=new Uint8Array(e),r=Mt(n,t);if(r)return r;let i=At(ie(n,{filter:({name:e})=>jt(e)})),a,o;if(i)o=Et(i,t),a=ie(n,{filter:({name:e})=>!jt(e)});else{if(a=ie(n),i=At(a),!i)throw Error(`No canvas data found in .fig file. Entries: ${Object.keys(a).join(`, `)}`);o=Et(i,t)}let s=a[`meta.json`],c=Object.entries(a).filter(([e])=>e.startsWith(`images/`)&&e!==`images/`).map(([e,t])=>[e.slice(7),t]);return{...o,images:c,thumbnailPNG:a[`thumbnail.png`]??null,metaJSON:Object.hasOwn(a,`meta.json`)?new TextDecoder().decode(s):null}}let K=[`textData`,`derivedTextData`,`textUserLayoutVersion`,`textExplicitLayoutVersion`],q=[`strokeGeometry`,`vectorData`],Pt={fillStyleId:[`styleIdForFill`],strokeStyleId:[`styleIdForStrokeFill`],textStyleId:[`styleIdForText`],effectStyleId:[`styleIdForEffect`],gridStyleId:[`styleIdForGrid`],fills:[`fillPaints`,`backgroundPaints`,`backgroundColor`],strokes:[`strokePaints`],effects:[`effects`],blendMode:[`blendMode`],layoutGrids:[`layoutGrids`],guides:[`guides`],exportSettings:[`exportSettings`],cornerRadius:[`cornerRadius`],independentCorners:[`rectangleCornerRadiiIndependent`],topLeftRadius:[`rectangleTopLeftCornerRadius`,`rectangleCornerRadiiIndependent`],topRightRadius:[`rectangleTopRightCornerRadius`,`rectangleCornerRadiiIndependent`],bottomLeftRadius:[`rectangleBottomLeftCornerRadius`,`rectangleCornerRadiiIndependent`],bottomRightRadius:[`rectangleBottomRightCornerRadius`,`rectangleCornerRadiiIndependent`],cornerSmoothing:[`cornerSmoothing`],borderTopWeight:[`borderTopWeight`,...q],borderRightWeight:[`borderRightWeight`,...q],borderBottomWeight:[`borderBottomWeight`,...q],borderLeftWeight:[`borderLeftWeight`,...q],independentStrokeWeights:[`borderStrokeWeightsIndependent`,`borderTopWeight`,`borderRightWeight`,`borderBottomWeight`,`borderLeftWeight`,...q],strokeWeight:[`strokeWeight`,...q],strokeJoin:[`strokeJoin`,...q],strokeMiterLimit:[`miterLimit`,...q],strokeCap:[...q],dashPattern:[...q],text:[...K],styleRuns:[...K],fontSize:[`fontSize`,...K],fontFamily:[`fontName`,`fontVersion`,...K],fontWeight:[`semanticWeight`,...K],italic:[`semanticItalic`,...K],textAlignHorizontal:[`textAlignHorizontal`,...K],textAlignVertical:[`textAlignVertical`,...K],lineHeight:[`lineHeight`,...K],letterSpacing:[`letterSpacing`,`textTracking`,...K],textAutoResize:[`textAutoResize`,...K],textDecorationStyle:[`textDecorationStyle`,...K],textDecorationThickness:[`textDecorationThickness`,...K],textDecorationFills:[`textDecorationFillPaints`,...K],textUnderlineOffset:[`textUnderlineOffset`,...K],leadingTrim:[`leadingTrim`,...K],maxLines:[`maxLines`,...K],fontVariations:[`fontVariations`,...K],fontFeatures:[`fontVariantCommonLigatures`,`fontVariantContextualLigatures`,`toggledOnOTFeatures`,`toggledOffOTFeatures`,...K],minWidth:[`minSize`],minHeight:[`minSize`],maxWidth:[`maxSize`],maxHeight:[`maxSize`],vectorNetwork:[`vectorData`,`fillGeometry`,`strokeGeometry`],fillGeometry:[`fillGeometry`,`vectorData`],strokeGeometry:[`strokeGeometry`,`vectorData`],isMask:[`mask`],maskType:[`maskType`],maskIsOutline:[`maskIsOutline`],componentPropertyDefinitions:[`componentPropDefs`],componentPropertyReferences:[`componentPropRefs`],componentPropertyAssignments:[`componentPropAssignments`],variantPropSpecs:[`variantPropSpecs`]};function Ft(e=[]){return new Set(e.flatMap(e=>Pt[e]??[]))}function It(e,t){if(!Ft(e.source.editedFields??[]).has(t))return e.source.fig.rawNodeFields[t]}let Lt=typeof globalThis==`object`&&globalThis||typeof window==`object`&&window||typeof self==`object`&&self||typeof global==`object`&&global||(function(){return this})();function Rt(e){return Lt.Buffer!==void 0&&Lt.Buffer.isBuffer(e)}function zt(e){if(!e||typeof e!=`object`)return!1;let t=Object.getPrototypeOf(e);return t===null||t===Object.prototype||Object.getPrototypeOf(t)===null?Object.prototype.toString.call(e)===`[object Object]`:!1}function Bt(e){return Object.getOwnPropertySymbols(e).filter(t=>Object.prototype.propertyIsEnumerable.call(e,t))}function Vt(e){return e==null?e===void 0?`[object Undefined]`:`[object Null]`:Object.prototype.toString.call(e)}let Ht=`[object Object]`;function Ut(e,t){return e===t||Number.isNaN(e)&&Number.isNaN(t)}function Wt(e,t,n){return Gt(e,t,void 0,void 0,void 0,void 0,n)}function Gt(e,t,n,r,i,a,o){let s=o(e,t,n,r,i,a);if(s!==void 0)return s;if(typeof e==typeof t)switch(typeof e){case`bigint`:case`string`:case`boolean`:case`symbol`:case`undefined`:return e===t;case`number`:return e===t||Object.is(e,t);case`function`:return e===t;case`object`:return Kt(e,t,a,o)}return Kt(e,t,a,o)}function Kt(e,t,n,r){if(Object.is(e,t))return!0;let i=Vt(e),a=Vt(t);if(i===`[object Arguments]`&&(i=Ht),a===`[object Arguments]`&&(a=Ht),i!==a)return!1;switch(i){case`[object String]`:return e.toString()===t.toString();case`[object Number]`:return Ut(e.valueOf(),t.valueOf());case`[object Boolean]`:case`[object Date]`:case`[object Symbol]`:return Object.is(e.valueOf(),t.valueOf());case`[object RegExp]`:return e.source===t.source&&e.flags===t.flags;case`[object Function]`:return e===t}n??=new Map;let o=n.get(e),s=n.get(t);if(o!=null&&s!=null)return o===t;n.set(e,t),n.set(t,e);try{switch(i){case`[object Map]`:if(e.size!==t.size)return!1;for(let[i,a]of e.entries())if(!t.has(i)||!Gt(a,t.get(i),i,e,t,n,r))return!1;return!0;case`[object Set]`:{if(e.size!==t.size)return!1;let i=Array.from(e.values()),a=Array.from(t.values());for(let o=0;o<i.length;o++){let s=i[o],c=a.findIndex(i=>Gt(s,i,void 0,e,t,n,r));if(c===-1)return!1;a.splice(c,1)}return!0}case`[object Array]`:case`[object Uint8Array]`:case`[object Uint8ClampedArray]`:case`[object Uint16Array]`:case`[object Uint32Array]`:case`[object BigUint64Array]`:case`[object Int8Array]`:case`[object Int16Array]`:case`[object Int32Array]`:case`[object BigInt64Array]`:case`[object Float32Array]`:case`[object Float64Array]`:if(Rt(e)!==Rt(t)||e.length!==t.length)return!1;for(let i=0;i<e.length;i++)if(!Gt(e[i],t[i],i,e,t,n,r))return!1;return!0;case`[object ArrayBuffer]`:return e.byteLength===t.byteLength&&Kt(new Uint8Array(e),new Uint8Array(t),n,r);case`[object DataView]`:return e.byteLength!==t.byteLength||e.byteOffset!==t.byteOffset?!1:Kt(new Uint8Array(e),new Uint8Array(t),n,r);case`[object Error]`:return e.name===t.name&&e.message===t.message;case Ht:{if(!(Kt(e.constructor,t.constructor,n,r)||zt(e)&&zt(t)))return!1;let i=[...Object.keys(e),...Bt(e)],a=[...Object.keys(t),...Bt(t)];if(i.length!==a.length)return!1;for(let a=0;a<i.length;a++){let o=i[a],s=e[o];if(!Object.hasOwn(t,o))return!1;let c=t[o];if(!Gt(s,c,o,e,t,n,r))return!1}return!0}default:return!1}}finally{n.delete(e),n.delete(t)}}function qt(){}function Jt(e,t){return Wt(e,t,qt)}function Yt(e){return e!=null}function Xt(e){if(!e||typeof e!=`object`)return!1;let t=e;return Number.isFinite(t.sessionID)&&Number.isFinite(t.localID)}function Zt(e,t){return e?`fig-guide:${e.sessionID}:${e.localID}`:`guide:${t}`}function Qt(e){if(!Array.isArray(e))return[];let t=[];for(let[n,r]of e.entries()){if(!r||typeof r!=`object`)continue;let e=r;if(typeof e.offset!=`number`||!Number.isFinite(e.offset))continue;let i=Xt(e.guid)?e.guid:void 0;e.axis===`X`?t.push({id:Zt(i,n),axis:`x`,position:e.offset,...i?{figGuid:i}:{}}):e.axis===`Y`&&t.push({id:Zt(i,n),axis:`y`,position:e.offset,...i?{figGuid:i}:{}})}return t}function J(e){return`${e.sessionID}:${e.localID}`}function $t(){return{self:new Map,descendants:new Map}}function en(e){return{self:new Map([...e.self].map(([e,t])=>[e,structuredClone(t)])),descendants:new Map([...e.descendants].map(([e,t])=>[e,new Map([...t].map(([e,t])=>[e,structuredClone(t)]))]))}}function tn(e,t,n,r){return n===t?e.self.get(r):e.descendants.get(n)?.get(r)}function nn(e,t,n,r){return(n===t?e.self:e.descendants.get(n))?.has(r)??!1}function rn(e,t,n,r,i=!0){if(n===t){e.self.set(r,i);return}let a=e.descendants.get(n)??new Map;a.set(r,i),e.descendants.set(n,a)}function an(e){e.self.clear(),e.descendants.clear()}function on(){return{minX:1/0,minY:1/0,maxX:-1/0,maxY:-1/0}}function sn(e,t,n){e.minX=Math.min(e.minX,t),e.minY=Math.min(e.minY,n),e.maxX=Math.max(e.maxX,t),e.maxY=Math.max(e.maxY,n)}function cn(e){return e.minX===1/0?{x:0,y:0,width:0,height:0}:{x:e.minX,y:e.minY,width:e.maxX-e.minX,height:e.maxY-e.minY}}function ln(e){return e===0?0:e===1||e===2?1:e===3?2:e===4?3:null}function un(e){let t=on();for(let n of e){let e=n.commandsBlob,r=new DataView(e.buffer,e.byteOffset,e.byteLength),i=0;for(;i<e.length;){let n=e[i++],a=ln(n);if(a==null)break;for(let n=0;n<a&&!(i+8>e.length);n++)sn(t,r.getFloat32(i,!0),r.getFloat32(i+4,!0)),i+=8}}return t.minX===1/0?null:cn(t)}let dn={r:0,g:0,b:0,a:1};function fn(){return{format:null,id:null,orderKey:null,editedFields:[],fig:{rawSize:null,rawTransform:null,rawNodeFields:{},layout:null,symbolOverrides:[],componentPropAssignments:[],derivedSymbolData:[],derivedSymbolDataLayoutVersion:null,uniformScaleFactor:null}}}function pn(e,t,n={}){return{id:e(),type:t,name:t.charAt(0)+t.slice(1).toLowerCase(),parentId:null,childIds:[],x:0,y:0,width:100,height:100,rotation:0,source:fn(),derivedLayout:null,fills:t===`TEXT`?[{type:`SOLID`,color:dn,opacity:1,visible:!0}]:[],strokes:[],effects:[],layoutGrids:[],guides:[],fillStyleId:null,strokeStyleId:null,textStyleId:null,effectStyleId:null,gridStyleId:null,sharedStyleType:null,opacity:1,cornerRadius:0,topLeftRadius:0,topRightRadius:0,bottomRightRadius:0,bottomLeftRadius:0,independentCorners:!1,cornerSmoothing:0,visible:!0,locked:!1,clipsContent:!1,text:``,fontSize:14,fontFamily:`Inter`,fontWeight:400,italic:!1,textAlignHorizontal:`LEFT`,textDirection:`AUTO`,textLanguage:null,leadingTrim:`NONE`,lineHeight:null,letterSpacing:0,layoutMode:`NONE`,layoutDirection:`AUTO`,layoutWrap:`NO_WRAP`,primaryAxisAlign:`MIN`,counterAxisAlign:`MIN`,primaryAxisSizing:`FIXED`,counterAxisSizing:`FIXED`,itemSpacing:0,counterAxisSpacing:0,paddingTop:0,paddingRight:0,paddingBottom:0,paddingLeft:0,blendMode:`PASS_THROUGH`,layoutPositioning:`AUTO`,layoutGrow:0,layoutAlignSelf:`AUTO`,vectorNetwork:null,handleMirroring:`NONE`,fillGeometry:[],strokeGeometry:[],arcData:null,textAlignVertical:`TOP`,textAutoResize:`NONE`,textCase:`ORIGINAL`,textDecoration:`NONE`,textDecorationStyle:`SOLID`,textDecorationThickness:null,textDecorationFills:[],textDecorationSkipInk:!0,textUnderlineOffset:null,maxLines:null,styleRuns:[],fontVariations:[],fontFeatures:[],horizontalConstraint:`MIN`,verticalConstraint:`MIN`,strokeCap:`NONE`,strokeJoin:`MITER`,dashPattern:[],borderTopWeight:0,borderRightWeight:0,borderBottomWeight:0,borderLeftWeight:0,independentStrokeWeights:!1,strokeMiterLimit:4,minWidth:null,maxWidth:null,minHeight:null,maxHeight:null,isMask:!1,maskType:`ALPHA`,maskIsOutline:!1,gridTemplateColumns:[],gridTemplateRows:[],gridColumnGap:0,gridRowGap:0,gridPosition:null,counterAxisAlignContent:`AUTO`,itemReverseZIndex:!1,strokesIncludedInLayout:!1,expanded:!0,textTruncation:`DISABLED`,autoRename:!0,pointCount:5,starInnerRadius:.38,componentId:null,instanceOverrides:$t(),componentPropertyDefinitions:[],componentPropertyReferences:[],componentPropertyAssignments:{},componentPropertyValues:{},componentKey:null,sourceLibraryKey:null,publishId:null,overrideKey:null,sharedSymbolVersion:null,publishedVersion:null,librarySource:null,isPublishable:!1,isSymbolPublishable:!1,symbolDescription:``,symbolLinks:[],variantPropSpecs:[],boundVariables:{},variableModes:{},exportSettings:[],pluginData:[],pluginRelaunchData:[],internalOnly:!1,flipX:!1,flipY:!1,textPicture:null,derivedTextGlyphs:null,textPathData:null,textPathBox:null,...n}}let mn=new Set([`CANVAS`,`FRAME`,`GROUP`,`BOOLEAN_OPERATION`,`SECTION`,`COMPONENT`,`COMPONENT_SET`,`INSTANCE`]);function hn(e){return{vertices:e.vertices.map(e=>({...e})),segments:e.segments.map(e=>({...e,tangentStart:{...e.tangentStart},tangentEnd:{...e.tangentEnd}})),regions:e.regions.map(e=>({windingRule:e.windingRule,loops:e.loops.map(e=>[...e])}))}}function gn(e){let t={x:0,y:0};return{vertices:e.vertices,segments:e.segments.map(e=>({start:e.start,end:e.end,tangentStart:e.tangentStart??{...t},tangentEnd:e.tangentEnd??{...t}})),regions:e.regions??[]}}function _n(e){let t={...e,color:{...e.color}};return e.gradientStops&&(t.gradientStops=e.gradientStops.map(Mn)),e.gradientTransform&&(t.gradientTransform={...e.gradientTransform}),e.imageTransform&&(t.imageTransform={...e.imageTransform}),e.patternSpacing&&(t.patternSpacing={...e.patternSpacing}),e.noiseSize&&(t.noiseSize={...e.noiseSize}),t}function vn(e){let t={...e,color:{...e.color}};return e.dashPattern&&(t.dashPattern=[...e.dashPattern]),t}function yn(e){return{...e,color:{...e.color},offset:{...e.offset}}}function bn(e){return{...e,style:{...e.style,fills:e.style.fills?e.style.fills.map(_n):void 0,textDecorationFills:e.style.textDecorationFills?e.style.textDecorationFills.map(_n):void 0,fontVariations:e.style.fontVariations?e.style.fontVariations.map(e=>({...e})):void 0,fontFeatures:e.style.fontFeatures?e.style.fontFeatures.map(e=>({...e})):void 0}}}let xn=new WeakMap;function Y(e,t){return xn.set(t,xn.get(e)??e),t}function Sn(e,t){return e===t||(xn.get(e)??e)===(xn.get(t)??t)}function X(e){return e.map(_n)}function Cn(e){return e.map(vn)}function wn(e){return e.map(yn)}function Tn(e){return e.map(e=>({...e,color:e.color?{...e.color}:void 0}))}function En(e){return e.map(bn)}function Dn(e,t){return{windingRule:e.windingRule,commandsBlob:t,...e.fills?{fills:X(e.fills)}:{}}}function On(e){return e.map(e=>Dn(e,e.commandsBlob.slice()))}function kn(e,t,n,r,i,a=0,o=0){let s=e.slice(),c=new DataView(s.buffer,s.byteOffset,s.byteLength),l=0;for(;l<s.length;){let e=s[l++],u=ln(e);if(u==null)break;for(let e=0;e<u&&!(l+8>s.length);e++){let e=c.getFloat32(l,!0),s=c.getFloat32(l+4,!0);c.setFloat32(l,t*e+n*s+a,!0),c.setFloat32(l+4,r*e+i*s+o,!0),l+=8}}return s}function An(e,t,n,r,i,a=0,o=0){return e.map(e=>Dn(e,kn(e.commandsBlob,t,n,r,i,a,o)))}function jn(e,t,n){return t===1&&n===1?On(e):An(e,t,0,0,n)}function Z(e,t){if(e!==void 0)return e.length>0?t(e):[]}function Mn(e){return{color:{...e.color},position:e.position}}function Nn(e){return e?.map(e=>({...e}))??[]}function Pn(e){return e?.map(e=>({...e,variantOptions:e.variantOptions?[...e.variantOptions]:void 0,preferredValues:e.preferredValues?[...e.preferredValues]:void 0}))??[]}function Fn(e){return e?e.map(e=>({...e,commandsBlob:new Uint8Array(e.commandsBlob)})):null}function In(e){return{startingAngle:e.startingAngle,endingAngle:e.endingAngle,innerRadius:e.innerRadius}}function Ln(e,t,n=`deep`){let{id:r,parentId:i,childIds:a,...o}=e;return n===`fig-import`?{...o,...t===null?{}:{componentId:t},source:fn(),boundVariables:{...e.boundVariables},variableModes:{...e.variableModes},instanceOverrides:en(e.instanceOverrides),componentPropertyAssignments:{...e.componentPropertyAssignments},componentPropertyValues:{...e.componentPropertyValues}}:{...o,...t===null?{}:{componentId:t},boundVariables:{...e.boundVariables},variableModes:{...e.variableModes},instanceOverrides:en(e.instanceOverrides),fills:Z(e.fills,e=>Y(e,X(e))),strokes:Z(e.strokes,e=>Y(e,Cn(e))),effects:Z(e.effects,e=>Y(e,wn(e))),layoutGrids:Z(e.layoutGrids,Tn),guides:Z(e.guides,e=>e.map(e=>({...e}))),styleRuns:Z(e.styleRuns,e=>Y(e,En(e))),source:t===null?structuredClone(e.source):fn(),dashPattern:Z(e.dashPattern,e=>[...e]),fontVariations:Z(e.fontVariations,e=>e.map(e=>({...e}))),fontFeatures:Z(e.fontFeatures,e=>e.map(e=>({...e}))),textDecorationFills:Z(e.textDecorationFills,X),fillGeometry:Z(e.fillGeometry,On),strokeGeometry:Z(e.strokeGeometry,On),gridTemplateColumns:Nn(e.gridTemplateColumns),gridTemplateRows:Nn(e.gridTemplateRows),componentPropertyDefinitions:Pn(e.componentPropertyDefinitions),componentPropertyReferences:Nn(e.componentPropertyReferences),componentPropertyAssignments:{...e.componentPropertyAssignments},symbolLinks:Nn(e.symbolLinks),variantPropSpecs:Nn(e.variantPropSpecs),pluginData:Nn(e.pluginData),pluginRelaunchData:Nn(e.pluginRelaunchData),exportSettings:Nn(e.exportSettings),componentPropertyValues:{...e.componentPropertyValues},derivedLayout:e.derivedLayout?{...e.derivedLayout}:null,arcData:e.arcData?In(e.arcData):null,vectorNetwork:e.vectorNetwork?hn(e.vectorNetwork):null,textPicture:e.textPicture?new Uint8Array(e.textPicture):null,derivedTextGlyphs:e.derivedTextGlyphs?Y(e.derivedTextGlyphs,Fn(e.derivedTextGlyphs)??[]):null,textPathData:e.textPathData?structuredClone(e.textPathData):null,textPathBox:e.textPathBox?{...e.textPathBox}:null,gridPosition:e.gridPosition?{...e.gridPosition}:null}}let Rn=`telecode`,zn=`slot`;function Bn(e,t){if(!e)return null;let n=`${Rn}/${t}`,r=e.pluginData.find(e=>e.pluginId===`telecode`&&(e.key===n||e.key===t));return r?.value?r.value:null}function Vn(e,t){if(!t)return null;let n=Bn(t,zn);if(n&&t.type!==`INSTANCE`)return n;if(t.type===`INSTANCE`)return null;let r=t.componentId?e.getNode(t.componentId):void 0;return r&&r.type!==`INSTANCE`?Bn(r,zn):null}function Hn(e,t){let n=t?.parentId?e.getNode(t.parentId):void 0;for(;n;){if(n.type===`INSTANCE`)return n;if(n.type===`CANVAS`)return;n=n.parentId?e.getNode(n.parentId):void 0}}function Un(e){let t=Bn(e,`slots`);if(!t)return[];try{let e=JSON.parse(t);return Array.isArray(e.filled)?e.filled.filter(e=>typeof e==`string`):[]}catch{return[]}}function Wn(e,t){if(!t||t.type===`INSTANCE`)return!1;let n=Vn(e,t);return n?Un(Hn(e,t)).includes(n):!1}function Gn(e,t){let n=new Map,r=t=>{let i=e.getNode(t);if(i)for(let t of i.childIds){let i=e.getNode(t);if(!i||i.type===`INSTANCE`)continue;let a=Vn(e,i);a&&!n.has(a)&&n.set(a,i),r(t)}};return r(t.id),n}function Kn(e){let t=`${Rn}/in-slot`;return e.pluginData?.find(e=>e.pluginId===`telecode`&&(e.key===t||e.key===`in-slot`))?.value||null}function qn(e,t,n){let r=Gn(e,t).get(n);if(!r)return null;for(let t of[...r.childIds])e.deleteNode(t);return r}let Jn=[`name`,`text`,`fontSize`,`fontWeight`,`fontFamily`,`textDirection`],Yn=`width.height.minWidth.maxWidth.minHeight.maxHeight.fills.strokes.effects.opacity.cornerRadius.topLeftRadius.topRightRadius.bottomRightRadius.bottomLeftRadius.independentCorners.layoutMode.layoutDirection.layoutWrap.primaryAxisAlign.counterAxisAlign.primaryAxisSizing.counterAxisSizing.itemSpacing.counterAxisSpacing.paddingTop.paddingRight.paddingBottom.paddingLeft.gridTemplateColumns.gridTemplateRows.gridColumnGap.gridRowGap.gridPosition.clipsContent.independentStrokeWeights.borderTopWeight.borderRightWeight.borderBottomWeight.borderLeftWeight.boundVariables.variableModes`.split(`.`),Xn=[...Yn,...Jn];function Zn(e,t,n){e[t]=n}function Qn(e,t,n){if(n===`fills`)Zn(e,n,X(t.fills));else if(n===`strokes`)Zn(e,n,Cn(t.strokes));else if(n===`effects`)Zn(e,n,wn(t.effects));else if(n===`styleRuns`)Zn(e,n,En(t.styleRuns));else if(n===`boundVariables`)Zn(e,n,{...t.boundVariables});else if(n===`variableModes`)Zn(e,n,{...t.variableModes});else if(n===`gridPosition`)Zn(e,n,t.gridPosition?{...t.gridPosition}:null);else{let r=t[n];Zn(e,n,Array.isArray(r)?structuredClone(r):r)}}function $n(e,t,n,r=`deep`){if(t===n||e.isDescendant(n,t))return;let i=e.nodes.get(t);if(i)for(let t of i.childIds){let i=e.nodes.get(t);if(!i)continue;let a=e.createNode(i.type,n,Ln(i,t,r));i.childIds.length>0&&$n(e,t,a.id,r)}}function er(e,t,n,r){n.type===`INSTANCE`?rn(e,t,n.id,`sourceComponentId`,r):n.componentId=r}function tr(e,t,n,r,i,a,o){let s=new Map,c=new Map,l=new Map;for(let n of t.childIds){if(a.has(n))continue;let t=e.nodes.get(n);t&&l.set(t.type,(l.get(t.type)??0)+1)}for(let t of n.childIds){let n=e.nodes.get(t);if(!n||o.has(n.id))continue;c.set(n.type,(c.get(n.type)??0)+1);let r=s.get(n.type);r||(r=new Map,s.set(n.type,r));let i=r.get(n.name);i?i.push(n):r.set(n.name,[n])}for(let n of t.childIds){if(a.has(n))continue;let t=e.nodes.get(n);if(!t)continue;let u=s.get(t.type),d=c.get(t.type)??0;if(d>(l.get(t.type)??0))continue;let f=u?.get(t.name)?.shift();f&&(c.set(t.type,d-1),a.set(n,f),o.add(f.id),er(i,r,f,n))}}function nr(e,t,n,r,i){let a=new Map;for(let e=0;e<r.length;e++)a.set(r[e],e);let o=new Map;for(let s=0;s<t.childIds.length;s++){let c=t.childIds[s],l=e.nodes.get(c),u=l?tn(i,n,l.id,`sourceComponentId`):void 0,d=typeof u==`string`?u:l?.componentId,f=d?a.get(d):void 0;o.set(c,f??r.length+s)}t.childIds.sort((e,t)=>(o.get(e)??0)-(o.get(t)??0))}function rr(e,t,n){return t===n||e.isDescendant(n,t)}function ir(e,t,n,r){if(rr(e,t,n))return;let i=e.nodes.get(t),a=e.nodes.get(n);if(!i||!a)return;let o=new Map,s=new Set,c=new Set(i.childIds);for(let t of a.childIds){let i=e.nodes.get(t);if(!i)continue;let a=tn(r,n,i.id,`sourceComponentId`),l=typeof a==`string`?a:i.componentId;l&&c.has(l)&&(o.set(l,i),s.add(i.id))}tr(e,i,a,n,r,o,s);for(let t of i.childIds)if(!o.has(t)){let r=e.nodes.get(t);if(!r)continue;let i=e.createNode(r.type,n,Ln(r,t));r.childIds.length>0&&$n(e,t,i.id),o.set(t,i),s.add(i.id)}for(let t of i.childIds){let i=e.nodes.get(t),a=o.get(t);if(!(!i||!a)){for(let e of Xn)nn(r,n,a.id,e)||Qn(a,i,e);i.childIds.length>0&&!nn(r,n,a.id,`componentId`)&&!Wn(e,a)&&ir(e,t,a.id,r)}}nr(e,a,n,i.childIds,r)}function ar(e){let t={};for(let n of Yn)Qn(t,e,n);return t}function or(e,t,n,r={}){let i=e.nodes.get(t);if(i?.type!==`COMPONENT`)return null;let a={...ar(i),name:i.name,componentId:t},o=e.createNode(`INSTANCE`,n,{...a,...r});return $n(e,i.id,o.id),o}function sr(e,t,n,r=`deep`){let i=e.nodes.get(t),a=e.nodes.get(n);!i||!a||i.type!==`INSTANCE`||$n(e,n,t,r)}function cr(e,t,n){let r=e.nodes.get(t),i=e.nodes.get(n);if(!r||i?.type!==`COMPONENT`||r.type!==`INSTANCE`)return;let a=r.componentId?e.nodes.get(r.componentId):void 0,o={componentId:n};for(let e of Yn)nn(r.instanceOverrides,r.id,r.id,e)||Qn(o,i,e);(!a||r.name===a.name)&&(o.name=i.name);let s=Array.from(r.childIds);for(let t of s)e.deleteNode(t);e.updateNode(t,o),$n(e,n,t)}let lr=new WeakMap;function ur(e,t){let n=e.nodes.get(t);if(n?.type!==`COMPONENT`)return;let r=lr.get(e);if(r||(r=new Set,lr.set(e,r)),!r.has(t)){r.add(t);try{for(let r of pr(e,t)){for(let e of Yn)nn(r.instanceOverrides,r.id,r.id,e)||Qn(r,n,e);ir(e,n.id,r.id,r.instanceOverrides)}}finally{r.delete(t)}}}function dr(e,t){let n=e.nodes.get(t);n?.type===`INSTANCE`&&(n.componentId&&e.instanceIndex.get(n.componentId)?.delete(t),n.type=`FRAME`,n.componentId=null,an(n.instanceOverrides))}function fr(e,t){let n=e.nodes.get(t);if(n?.componentId)return e.nodes.get(n.componentId)}function pr(e,t){let n=e.instanceIndex.get(t);if(!n)return[];let r=[];for(let t of n){let n=e.nodes.get(t);n&&r.push(n)}return r}function mr(e,t){let n=e.nodes.get(t);for(;n;){if(n.type===`INSTANCE`)return n;n=n.parentId?e.nodes.get(n.parentId):void 0}}function hr(e,t,n){let r=mr(e,t);return r?nn(r.instanceOverrides,r.id,t,n):!1}function gr(e){return Math.min(1024,Math.max(.01,e))}let _r=()=>[1,0,0,0,1,0,0,0,1],vr=(e,t)=>[e[0]*t[0]+e[1]*t[3]+e[2]*t[6],e[0]*t[1]+e[1]*t[4]+e[2]*t[7],e[0]*t[2]+e[1]*t[5]+e[2]*t[8],e[3]*t[0]+e[4]*t[3]+e[5]*t[6],e[3]*t[1]+e[4]*t[4]+e[5]*t[7],e[3]*t[2]+e[4]*t[5]+e[5]*t[8],e[6]*t[0]+e[7]*t[3]+e[8]*t[6],e[6]*t[1]+e[7]*t[4]+e[8]*t[7],e[6]*t[2]+e[7]*t[5]+e[8]*t[8]],yr=(...e)=>{if(e.length===0)return _r();let t=e[0].slice();for(let n=1;n<e.length;n++)t=vr(t,e[n]);return t},br=(e,t)=>[1,0,e,0,1,t,0,0,1],xr=(e,t=0,n=0)=>{let r=Math.sin(e),i=Math.cos(e);return[i,-r,r*n+(1-i)*t,r,i,-r*t+(1-i)*n,0,0,1]},Sr=(e,t,n=0,r=0)=>[e,0,n-e*n,0,t,r-t*r,0,0,1],Cr=e=>{let t=e[0]*e[4]*e[8]+e[1]*e[5]*e[6]+e[2]*e[3]*e[7]-e[2]*e[4]*e[6]-e[1]*e[3]*e[8]-e[0]*e[5]*e[7];return t?[(e[4]*e[8]-e[5]*e[7])/t,(e[2]*e[7]-e[1]*e[8])/t,(e[1]*e[5]-e[2]*e[4])/t,(e[5]*e[6]-e[3]*e[8])/t,(e[0]*e[8]-e[2]*e[6])/t,(e[2]*e[3]-e[0]*e[5])/t,(e[3]*e[7]-e[4]*e[6])/t,(e[1]*e[6]-e[0]*e[7])/t,(e[0]*e[4]-e[1]*e[3])/t]:null},wr=(e,t)=>{if(t.length%2)throw Error(`mapPoints requires even length [x,y,...].`);let n=t.slice();for(let t=0;t<n.length;t+=2){let r=n[t],i=n[t+1],a=e[6]*r+e[7]*i+e[8],o=e[0]*r+e[1]*i+e[2],s=e[3]*r+e[4]*i+e[5];n[t]=o/a,n[t+1]=s/a}return n},Q={identity:_r,multiply:yr,translated:br,rotated:xr,scaled:Sr,invert:Cr,mapPoints:wr,mapPoint:(e,t)=>{let n=wr(e,[t.x,t.y]);return{x:n[0],y:n[1]}}};function Tr(e,t){let n=[],r=e;for(;r&&(n.unshift(r),r.parentId);)r=t.getNode(r.parentId);let i=Q.identity();for(let e of n){let t=Or(e);i=Q.multiply(i,t)}return i}function Er(e,t){let n=Tr(e,t),r=Q.mapPoints(n,[0,0]);return{x:r[0],y:r[1]}}function Dr(e){return e.type===`LINE`?{x:0,y:0}:{x:e.width/2,y:e.height/2}}function Or(e,t=e){let n=Q.translated(t.x,t.y);if((e.flipX||e.flipY)&&(n=Q.multiply(n,Q.scaled(e.flipX?-1:1,e.flipY?-1:1,e.width/2,e.height/2))),e.rotation){let t=Dr(e);n=Q.multiply(n,Q.rotated(e.rotation*Math.PI/180,t.x,t.y))}return n}let kr=new Map([{weight:100,names:[`thin`,`hairline`,`extrathin`,`ultrathin`]},{weight:200,names:[`extralight`,`ultralight`]},{weight:300,names:[`light`]},{weight:400,names:[`regular`,`normal`,`book`,`roman`,`plain`]},{weight:500,names:[`medium`]},{weight:600,names:[`semibold`,`demibold`]},{weight:700,names:[`bold`]},{weight:800,names:[`extrabold`,`ultrabold`]},{weight:900,names:[`black`,`heavy`]}].flatMap(({names:e,weight:t})=>e.map(e=>[e,t])));function Ar(e){return e.toLowerCase().replace(/italic|oblique/u,``).replace(/[^a-z0-9]+/gu,``)}function jr(e){let t=e??``,n=/(?:italic|oblique)/iu.test(t),r=Ar(t),i=r.match(/(?:^|[^0-9])([1-9]00)(?:[^0-9]|$)/u)?.[1];return{weight:i?Number(i):kr.get(r)??400,italic:n}}function Mr(e){return jr(e).weight}let Nr=new Set([`fontFamily`,`fontWeight`,`italic`,`fontSize`,`lineHeight`,`letterSpacing`,`textDecoration`,`textCase`,`fontFeatures`]);function Pr(e,t){let n={...t};return`fills`in t&&!(`fillStyleId`in t)&&e.fillStyleId&&(n.fillStyleId=null),`strokes`in t&&!(`strokeStyleId`in t)&&e.strokeStyleId&&(n.strokeStyleId=null),`effects`in t&&!(`effectStyleId`in t)&&e.effectStyleId&&(n.effectStyleId=null),`layoutGrids`in t&&!(`gridStyleId`in t)&&e.gridStyleId&&(n.gridStyleId=null),Object.keys(t).some(e=>Nr.has(e))&&!(`textStyleId`in t)&&e.textStyleId&&(n.textStyleId=null),n}let Fr=()=>({emit(e,...t){for(let n=this.events[e]||[],r=0,i=n.length;r<i;r++)n[r](...t)},events:{},on(e,t){return(this.events[e]||=[]).push(t),()=>{this.events[e]=this.events[e]?.filter(e=>t!==e)}}});function Ir(e,t){let n={...e};for(let e=0;e<t.length;e++){let r=t[e];delete n[r]}return n}function Lr(e,t){let n={},r=Object.keys(e);for(let i=0;i<r.length;i++){let a=r[i],o=e[a];t(o,a)||(n[a]=o)}return n}function Rr(e,t,n){let r=e[t].length,i=Object.keys(e.boundVariables).filter(e=>{if(e===t)return!0;if(!e.startsWith(`${t}/`))return!1;let n=Number.parseInt(e.split(`/`)[1]??``,10);return Number.isNaN(n)||n<0||n>=r});i.length!==0&&(e.boundVariables=Ir(e.boundVariables,i),n.boundVariables={...e.boundVariables})}function zr(e,t){let n=[t.created?e.on(`node:created`,t.created):null,t.updated?e.on(`node:updated`,t.updated):null,t.previewUpdated?e.on(`node:previewUpdated`,t.previewUpdated):null,t.deleted?e.on(`node:deleted`,t.deleted):null,t.reparented?e.on(`node:reparented`,t.reparented):null,t.reordered?e.on(`node:reordered`,t.reordered):null].filter(e=>!!e);return()=>{for(let e of n)e()}}let Br=new Set([`CANVAS`,`FRAME`,`GROUP`,`SECTION`,`COMPONENT`,`COMPONENT_SET`,`INSTANCE`]),Vr=new Set([`COMPONENT`,`INSTANCE`]);function Hr(e){return e.fills.some(e=>e.visible)||e.strokes.some(e=>e.visible)}function Ur(e,t,n){let r=n.get(e.id);if(r!==void 0)return r;let i=e.parentId?t.getNode(e.parentId):void 0,a=e.rotation!==0||e.flipX||e.flipY||(i?Ur(i,t,n):!1);return n.set(e.id,a),a}function Wr(e,t,n,r,i){if(!Ur(n,r,i)){let i=r.getAbsolutePosition(n.id);return e>=i.x&&e<=i.x+n.width&&t>=i.y&&t<=i.y+n.height}let a=Tr(n,r),o=Q.invert(a);if(!o)return!1;let[s,c]=Q.mapPoints(o,[e,t]);return s>=0&&s<=n.width&&c>=0&&c<=n.height}function Gr(e,t,n,r,i,a,o){return Wr(t,n,r,e,o)&&(qr(e,t,n,i,a,o)||Hr(r))?r:null}function Kr(e,t,n,r,i,a,o){if(r.type===`GROUP`)return Wr(t,n,r,e,o)?a?qr(e,t,n,i,a,o)??r:r:null;let s=qr(e,t,n,i,a,o);return s?r.locked?r:s:Wr(t,n,r,e,o)&&Hr(r)?r:null}function qr(e,t,n,r,i=!1,a=new Map){let o=e.nodes.get(r);if(!o||o.clipsContent&&!Wr(t,n,o,e,a))return null;for(let r=o.childIds.length-1;r>=0;r--){let s=o.childIds[r],c=e.nodes.get(s);if(!(!c||c.internalOnly||!c.visible)){if(Br.has(c.type)){if(Vr.has(c.type)&&!i){let r=Gr(e,t,n,c,s,i,a);if(r)return r;continue}let r=Kr(e,t,n,c,s,i,a);if(r)return r;continue}if(Wr(t,n,c,e,a))return c}}return null}function Jr(e,t,n,r){return qr(e,t,n,r??e.rootId,!1)}function Yr(e,t,n,r){return qr(e,t,n,r??e.rootId,!0)}function Xr(e,t,n,r,i,a,o){let s=e.nodes.get(r);if(!s)return null;let c=null;for(let r of s.childIds){if(o.has(r))continue;let s=e.nodes.get(r);if(!s||s.internalOnly||!s.visible)continue;let l=i+s.x,u=a+s.y;if(!Br.has(s.type)||t<l||t>l+s.width||n<u||n>u+s.height)continue;c=s;let d=Xr(e,t,n,r,l,u,o);d&&(c=d)}return c}function Zr(e,t,n,r,i){return Xr(e,t,n,i??e.rootId,0,0,r)}let Qr=new Set([`text`,`fontSize`,`fontFamily`,`fontWeight`,`italic`,`textAlignHorizontal`,`textDirection`,`textAlignVertical`,`lineHeight`,`letterSpacing`,`textDecoration`,`textCase`,`styleRuns`,`fills`,`width`,`height`]),$r=new Set([`text`,`fontSize`,`fontFamily`,`fontWeight`,`italic`,`textDirection`,`lineHeight`,`letterSpacing`,`textCase`,`styleRuns`]);function ei(e,t){let n={},r=Object.keys(t);e.textPicture&&r.some(e=>Qr.has(e))&&(n.textPicture=null);let i=r.some(e=>$r.has(e));return e.derivedTextGlyphs&&i&&!t.derivedTextGlyphs&&(n.derivedTextGlyphs=null,n.textPathData=null),n}function ti(e,t){Object.assign(e,ei(e,t))}let ni=new Set(`x.y.width.height.rotation.flipX.flipY.parentId.childIds.layoutMode.layoutDirection.layoutWrap.primaryAxisSizing.counterAxisSizing.itemSpacing.counterAxisSpacing.paddingTop.paddingRight.paddingBottom.paddingLeft.layoutGrow.layoutAlignSelf.layoutPositioning.minWidth.maxWidth.minHeight.maxHeight.visible.text.fontSize.lineHeight.letterSpacing.styleRuns.textAutoResize`.split(`.`));function ri(e,t,n,r){let i=e.nodes.get(t);if(!i||(n=Object.fromEntries(Object.entries(n).filter(([,e])=>e!==void 0)),Object.keys(n).every(e=>i[e]===n[e])))return null;Object.keys(n).some(e=>ni.has(e))&&e.clearAbsPosCache();let a=n;return i.type===`TEXT`&&(a={...ei(i,n),...n}),n.vectorNetwork&&(a={...a,vectorNetwork:gn(n.vectorNetwork)}),r?.(i,a),e.positionPreviewVersion++,Object.assign(i,a),a}function ii(e,t){if(t.length===0)return;let n=new Set(e.source.editedFields);for(let e of t)n.add(e);e.source.editedFields=[...n]}function ai(e,t){e.variables.set(t.id,t);let n=e.variableCollections.get(t.collectionId);n&&!n.variableIds.includes(t.id)&&n.variableIds.push(t.id)}function oi(e,t){let n=e.variables.get(t);if(!n)return;e.variables.delete(t);let r=e.variableCollections.get(n.collectionId);r&&(r.variableIds=r.variableIds.filter(e=>e!==t));for(let n of e.nodes.values())Object.values(n.boundVariables).includes(t)&&(n.boundVariables=Lr(n.boundVariables,e=>e===t),e.emitter.emit(`node:updated`,n.id,{boundVariables:{...n.boundVariables}}),Mi(e,n.id))}function si(e,t){e.variableCollections.set(t.id,t),e.activeMode.has(t.id)||e.activeMode.set(t.id,t.defaultModeId)}function ci(e,t){return t===void 0?e===`COLOR`?{...dn}:e===`FLOAT`?0:e!==`BOOLEAN`&&``:t}function li(e,t,n,r,i,a){let o=e.variableCollections.get(i);if(!o)throw Error(`Collection "${i}" not found`);let s=t(),c=ci(r,a),l={};for(let e of o.modes)l[e.modeId]=structuredClone(c);let u={id:s,name:n,type:r,collectionId:i,valuesByMode:l,description:``,hiddenFromPublishing:!1};return ai(e,u),u}function ui(e,t,n){let r=t(),i=t(),a={id:r,name:n,modes:[{modeId:i,name:`Mode 1`}],defaultModeId:i,variableIds:[]};return si(e,a),a}function di(e,t){let n=e.variableCollections.get(t);if(n)for(let t of Array.from(n.variableIds))oi(e,t);e.variableCollections.delete(t),e.activeMode.delete(t)}function fi(e,t){return e.activeMode.get(t)||(e.variableCollections.get(t)?.defaultModeId??``)}function pi(e,t,n){let r=e.nodes.get(t);for(;r;){let t=r.variableModes[n];if(t)return t;r=r.parentId?e.nodes.get(r.parentId):void 0}return fi(e,n)}function mi(e,t,n){e.activeMode.set(t,n)}function hi(e,t,n,r,i){let a=e.variableCollections.get(t);if(!a)return;a.modes.push({modeId:n,name:r});let o=i??a.defaultModeId;for(let t of a.variableIds){let r=e.variables.get(t);r&&(r.valuesByMode[n]=structuredClone(r.valuesByMode[o]??Object.values(r.valuesByMode)[0]))}}function gi(e,t,n){let r=e.variableCollections.get(t);if(!(!r||r.modes.length<=1)){r.modes=r.modes.filter(e=>e.modeId!==n),r.defaultModeId===n&&(r.defaultModeId=r.modes[0].modeId);for(let t of r.variableIds){let r=e.variables.get(t);r&&(r.valuesByMode=Ir(r.valuesByMode,[n]))}e.activeMode.get(t)===n&&e.activeMode.set(t,r.defaultModeId)}}function _i(e,t,n,r){let i=e.variableCollections.get(t);if(!i)return;let a=i.modes.find(e=>e.modeId===n);a&&(a.name=r)}function vi(e,t,n){let r=e.variableCollections.get(t);r&&r.modes.some(e=>e.modeId===n)&&(r.defaultModeId=n)}function yi(e,t,n,r,i){if(r?.has(t))return;let a=e.variables.get(t);if(!a)return;let o=e.variableCollections.get(a.collectionId),s=n??fi(e,a.collectionId),c=o?.defaultModeId,l=Object.hasOwn(a.valuesByMode,s)?a.valuesByMode[s]:void 0;if(l===void 0&&c&&Object.hasOwn(a.valuesByMode,c)&&(l=a.valuesByMode[c]),l??=Object.values(a.valuesByMode)[0],l&&typeof l==`object`&&`aliasId`in l){let n=r??new Set;n.add(t);let o=e.variables.get(l.aliasId),c=o&&o.collectionId!==a.collectionId?i?.(o.collectionId)??fi(e,o.collectionId):s;return yi(e,l.aliasId,c,n,i)}return l}function bi(e,t,n){let r=e.variables.get(n);if(!r)return;let i=n=>pi(e,t,n);return yi(e,n,i(r.collectionId),void 0,i)}function xi(e,t){let n=yi(e,t);if(n&&typeof n==`object`&&`r`in n)return n}function Si(e,t){let n=yi(e,t);return typeof n==`number`?n:void 0}function Ci(e,t,n){let r=bi(e,t,n);if(r&&typeof r==`object`&&`r`in r)return r}function wi(e,t,n){let r=bi(e,t,n);return typeof r==`number`?r:void 0}function Ti(e,t){let n=e.variableCollections.get(t);return n?n.variableIds.map(t=>e.variables.get(t)).filter(e=>e!==void 0):[]}function Ei(e,t){return[...e.variables.values()].filter(e=>e.type===t)}let Di=new Set(`opacity.width.height.cornerRadius.fontSize.letterSpacing.lineHeight.itemSpacing.strokeWeight.paddingLeft.paddingRight.paddingTop.paddingBottom.counterAxisSpacing.topLeftRadius.topRightRadius.bottomLeftRadius.bottomRightRadius.rotation.x.y.minWidth.maxWidth.minHeight.maxHeight.borderTopWeight.borderBottomWeight.borderLeftWeight.borderRightWeight.gridRowGap.gridColumnGap`.split(`.`)),Oi=new Set([`fontFamily`]),ki=new Set([`visible`]);function Ai(e,t,n,r){let i=e.nodes.get(t);if(!i)return;let a=e.variables.get(r);if(!a)throw Error(`Variable "${r}" not found`);let o=n.match(/^(fills|strokes)\/(\d+)\/color$/);if(o){if(a.type!==`COLOR`)throw Error(`Cannot bind ${a.type} variable to color field "${n}"`);let e=o[1],t=Number.parseInt(o[2],10),r=i[e]?.length??0;if(t>=r)throw Error(`Index ${t} out of range for ${e} (length ${r})`);let s=o[1];s in i.boundVariables&&(i.boundVariables=Ir(i.boundVariables,[s]))}if(Di.has(n)&&a.type!==`FLOAT`)throw Error(`Cannot bind ${a.type} variable to scalar field "${n}"`);if(Oi.has(n)&&a.type!==`STRING`)throw Error(`Cannot bind ${a.type} variable to string field "${n}"`);if(ki.has(n)&&a.type!==`BOOLEAN`)throw Error(`Cannot bind ${a.type} variable to boolean field "${n}"`);if(!(Di.has(n)||Oi.has(n)||ki.has(n)||o))throw Error(`Unknown binding field "${n}"`);i.boundVariables={...i.boundVariables,[n]:r},e.emitter.emit(`node:updated`,t,{boundVariables:{...i.boundVariables}}),Mi(e,t)}function ji(e,t,n){let r=e.nodes.get(t);r&&n in r.boundVariables&&(r.boundVariables=Ir(r.boundVariables,[n]),e.emitter.emit(`node:updated`,t,{boundVariables:{...r.boundVariables}}),Mi(e,t))}function Mi(e,t){let n=e.nodes.get(t);if(!n)return;if(n.type===`INSTANCE`){rn(n.instanceOverrides,n.id,n.id,`boundVariables`);return}let r=n;for(;r.parentId;){let n=e.nodes.get(r.parentId);if(!n)break;if(n.type===`INSTANCE`){rn(n.instanceOverrides,n.id,t,`boundVariables`);break}r=n}}let Ni=1;function Pi(){return`0:${Ni++}`}function Fi(e){let t={};for(let n of Object.keys(e)){let r=e[n];r!==void 0&&(t[n]=r)}return t}var Ii=class e{nodes=new Map;images=new Map;variables=new Map;variableCollections=new Map;activeMode=new Map;rootId;figKiwiVersion=null;figSchemaDeflated=null;documentColorSpace=`srgb`;enabledLibraries=new Map;emitter=Fr();absPosCache=new Map;previewMutationDepth=0;previewObservers=[];sourceMetadataPreservationDepth=0;layoutMutationDepth=0;positionPreviewVersion=0;instanceIndex=new Map;constructor(){let e=pn(Pi,`FRAME`,{name:`Document`,width:0,height:0});this.rootId=e.id,this.nodes.set(e.id,e),this.addPage(`Page 1`)}addPage(e){return this.createNode(`CANVAS`,this.rootId,{name:e,width:0,height:0})}getPages(e=!1){return this.getChildren(this.rootId).filter(t=>t.type===`CANVAS`&&(e||!t.internalOnly))}getAllNodes(){return this.nodes.values()}getNode(e){return this.nodes.get(e)}onNodeEvents(e){return zr(this.emitter,e)}countDescendants(e){let t=this.nodes.get(e);if(!t)return 0;let n=0,r=[...t.childIds];for(;r.length>0;){let e=r.pop();if(e===void 0)break;n++;let t=this.nodes.get(e);if(t)for(let e of t.childIds)r.push(e)}return n}addVariable(e){ai(this,e)}removeVariable(e){oi(this,e)}addCollection(e){si(this,e)}createVariable(e,t,n,r){return li(this,Pi,e,t,n,r)}createCollection(e){return ui(this,Pi,e)}removeCollection(e){di(this,e)}getActiveModeId(e){return fi(this,e)}getNodeVariableModeId(e,t){return pi(this,e,t)}setActiveMode(e,t){mi(this,e,t)}addMode(e,t,n,r){hi(this,e,t,n,r)}removeMode(e,t){gi(this,e,t)}renameMode(e,t,n){_i(this,e,t,n)}setDefaultMode(e,t){vi(this,e,t)}resolveVariable(e,t,n){return yi(this,e,t,n)}resolveColorVariable(e){return xi(this,e)}resolveNumberVariable(e){return Si(this,e)}resolveColorVariableForNode(e,t){return Ci(this,e,t)}resolveNumberVariableForNode(e,t){return wi(this,e,t)}resolveVariableForNode(e,t){return bi(this,e,t)}getVariablesForCollection(e){return Ti(this,e)}getVariablesByType(e){return Ei(this,e)}bindVariable(e,t,n){Ai(this,e,t,n)}unbindVariable(e,t){ji(this,e,t)}getChildren(e){let t=this.nodes.get(e);return t?t.childIds.map(e=>this.nodes.get(e)).filter(e=>e!==void 0):[]}isContainer(e){let t=this.nodes.get(e);return t?mn.has(t.type):!1}isDescendant(e,t){let n=this.nodes.get(e);for(;n;){if(n.id===t)return!0;n=n.parentId?this.nodes.get(n.parentId):void 0}return!1}clearAbsPosCache(){this.absPosCache.clear()}getAbsolutePosition(e){let t=this.absPosCache.get(e);if(t)return t;let n=this.getNode(e);if(!n)return{x:0,y:0};let r=Er(n,this);return this.absPosCache.set(e,r),r}getAbsoluteBounds(e){let t=this.getAbsolutePosition(e),n=this.nodes.get(e);return{x:t.x,y:t.y,width:n?.width??0,height:n?.height??0}}generateNodeId(){let e=Pi();for(;this.nodes.has(e);)e=Pi();return e}registerNode(e,t){if(e.parentId=t,this.nodes.set(e.id,e),e.type===`INSTANCE`&&e.componentId){let t=this.instanceIndex.get(e.componentId);t||(t=new Set,this.instanceIndex.set(e.componentId,t)),t.add(e.id)}return this.emitter.emit(`node:created`,e),e}createNode(e,t,n={}){let r=pn(()=>this.generateNodeId(),e,n);return this.nodes.get(t)?.childIds.push(r.id),this.registerNode(r,t)}createNodeWithId(e,t,n,r={}){let i=pn(()=>e,t,r);i.id=e;let a=n?this.nodes.get(n):void 0;return a&&!a.childIds.includes(e)&&a.childIds.push(e),this.registerNode(i,n)}static TEXT_PICTURE_KEYS=Qr;static GLYPH_AFFECTING_KEYS=$r;static LAYOUT_AFFECTING_KEYS=new Set(`x.y.width.height.rotation.flipX.flipY.layoutMode.layoutDirection.itemSpacing.counterAxisSpacing.paddingLeft.paddingRight.paddingTop.paddingBottom.primaryAxisAlign.counterAxisAlign.counterAxisAlignContent.layoutWrap.primaryAxisSizing.counterAxisSizing.layoutPositioning.layoutGrow.layoutAlignSelf.strokesIncludedInLayout.horizontalConstraint.verticalConstraint.gridTemplateColumns.gridTemplateRows.gridColumnGap.gridRowGap.gridPosition.minWidth.maxWidth.minHeight.maxHeight`.split(`.`));runPreviewUpdates(e,t){this.previewMutationDepth++,t&&this.previewObservers.push(t);try{e()}finally{t&&this.previewObservers.pop(),this.previewMutationDepth--}}preserveSourceMetadataDuring(e){this.sourceMetadataPreservationDepth++;try{e()}finally{this.sourceMetadataPreservationDepth--}}withLayoutMutations(e){this.layoutMutationDepth++;try{e()}finally{this.layoutMutationDepth--}}get isApplyingLayout(){return this.layoutMutationDepth>0}updateNodePositionPreview(e,t,n){this.updateNodePreview(e,{x:t,y:n})}updateNodePreview(e,t){let n=ri(this,e,t,(e,t)=>{for(let n of this.previewObservers)n(e,t)});n&&this.emitter.emit(`node:previewUpdated`,e,n)}updateNode(e,t){if(this.previewMutationDepth>0){this.updateNodePreview(e,t);return}let n=this.nodes.get(e);n&&(t=Fi(Pr(n,Fi(t))),this.applyNodeChanges(n,t))}restoreNodeProperties(e,t,n){let r=this.nodes.get(e);r&&this.applyNodeChanges(r,t,n)}applyNodeChanges(t,n,r=[]){let{id:i}=t;if(r.length){n={...n};for(let e of r)Reflect.set(n,e,void 0)}if(Object.keys(n).some(t=>e.LAYOUT_AFFECTING_KEYS.has(t))&&this.absPosCache.clear(),t.type===`INSTANCE`&&`componentId`in n&&n.componentId!==t.componentId&&(t.componentId&&this.instanceIndex.get(t.componentId)?.delete(i),n.componentId)){let e=this.instanceIndex.get(n.componentId);e||(e=new Set,this.instanceIndex.set(n.componentId,e)),e.add(i)}t.type===`TEXT`&&ti(t,n),this.sourceMetadataPreservationDepth===0&&ii(t,Object.keys(n)),n.vectorNetwork&&(n={...n,vectorNetwork:gn(n.vectorNetwork)}),Object.assign(t,n),n.fills&&Rr(t,`fills`,n),n.strokes&&Rr(t,`strokes`,n);for(let e of r)Reflect.deleteProperty(t,e);this.emitter.emit(`node:updated`,i,n)}reparentNode(e,t){let n=this.nodes.get(e);if(!n||e===this.rootId||this.isDescendant(t,e))return;let r=n.parentId?this.nodes.get(n.parentId):void 0,i=this.nodes.get(t);if(!i||n.parentId===t)return;let a=n.parentId;this.absPosCache.clear();let o=this.getAbsolutePosition(e),s=this.nodes.get(t),c=t===this.rootId||s?.type===`CANVAS`?{x:0,y:0}:this.getAbsolutePosition(t);r&&(r.childIds=r.childIds.filter(t=>t!==e)),n.parentId=t,i.childIds.push(e),n.x=o.x-c.x,n.y=o.y-c.y,this.emitter.emit(`node:reparented`,e,a,t)}reorderChild(e,t,n){let r=this.nodes.get(e);if(!r)return;let i=r.parentId,a=i?this.nodes.get(i):void 0,o=this.nodes.get(t);if(!o||this.isDescendant(t,e))return;a&&(a.childIds=a.childIds.filter(t=>t!==e));let s=n;a===o&&a.childIds.includes(e)&&a.childIds.length,r.parentId=t,this.absPosCache.clear(),s=Math.min(s,o.childIds.length),o.childIds.splice(s,0,e),this.emitter.emit(`node:reordered`,e,t,s,i)}insertChildAt(e,t,n){let r=this.getNode(e),i=this.getNode(t);if(!r||!i||e===t||this.isDescendant(t,e))return;let a=r.parentId,o=a?this.getNode(a):void 0;o&&(o.childIds=o.childIds.filter(t=>t!==e)),i.childIds=i.childIds.filter(t=>t!==e),i.childIds.splice(n,0,e),r.parentId=t,this.clearAbsPosCache(),this.emitter.emit(`node:reordered`,e,t,n,a)}deleteNode(e){let t=this.nodes.get(e);if(!(!t||e===this.rootId)){if(t.parentId){let n=this.nodes.get(t.parentId);n&&(n.childIds=n.childIds.filter(t=>t!==e))}for(let e of Array.from(t.childIds))this.deleteNode(e);t.type===`INSTANCE`&&t.componentId&&this.instanceIndex.get(t.componentId)?.delete(e),this.nodes.delete(e),this.emitter.emit(`node:deleted`,e,t.parentId)}}hitTest(e,t,n){return Jr(this,e,t,n)}hitTestDeep(e,t,n){return Yr(this,e,t,n)}hitTestFrame(e,t,n,r){return Zr(this,e,t,n,r)}cloneTree(e,t,n={}){let r=this.nodes.get(e);if(!r)return null;let i=Ln(r,null);i.source={...i.source,id:null,orderKey:null};let a=this.createNode(r.type,t,{...i,...n});for(let e of r.childIds)this.cloneTree(e,a.id);return a}createInstance(e,t,n={}){return or(this,e,t,n)}populateInstanceChildren(e,t,n=`deep`){sr(this,e,t,n)}swapInstanceComponent(e,t){cr(this,e,t)}syncInstances(e){ur(this,e)}detachInstance(e){dr(this,e)}getMainComponent(e){return fr(this,e)}getInstances(e){return pr(this,e)}flattenTree(e,t=0){let n=e??this.rootId,r=this.nodes.get(n);if(!r)return[];let i=[];for(let e of r.childIds){let n=this.nodes.get(e);n&&(i.push({node:n,depth:t}),n.childIds.length>0&&i.push(...this.flattenTree(e,t+1)))}return i}};function Li(e){let t={};for(let n of e.split(`,`).map(e=>e.trim())){let e=n.indexOf(`=`);e!==-1&&(t[n.slice(0,e).trim()]=n.slice(e+1).trim())}return t}function Ri(e,t){return(e?.glyphs??[]).map(e=>e.commandsBlob===void 0?null:{commandsBlob:t[e.commandsBlob],x:e.position.x,y:e.position.y,fontSize:e.fontSize,rotation:e.rotation}).filter(e=>!!e)}let zi=Ze(`enum MessageType {
  JOIN_START = 0;
  NODE_CHANGES = 1;
  USER_CHANGES = 2;
  JOIN_END = 3;
  SIGNAL = 4;
  STYLE = 5;
  STYLE_SET = 6;
  JOIN_START_SKIP_RELOAD = 7;
  NOTIFY_SHOULD_UPGRADE = 8;
  UPGRADE_DONE = 9;
  UPGRADE_REFRESH = 10;
  SCENE_GRAPH_QUERY = 11;
  SCENE_GRAPH_REPLY = 12;
  DIFF = 13;
  CLIENT_BROADCAST = 14;
  JOIN_START_JOURNALED = 15;
  STREAM_START = 16;
  STREAM_END = 17;
  INTERACTIVE_SLIDE_CHANGE = 18;
  RECONNECT_SCENE_GRAPH_QUERY = 19;
  RECONNECT_SCENE_GRAPH_REPLY = 20;
  JOIN_END_INCREMENTAL_RECONNECT = 21;
  NODE_STATUS_CHANGE = 22;
  CLIENT_RENDERED = 23;
  BUZZ_APPROVAL_CHANGE = 24;
}

enum Axis {
  X = 0;
  Y = 1;
}

enum Access {
  READ_ONLY = 0;
  READ_WRITE = 1;
}

enum NodePhase {
  CREATED = 0;
  REMOVED = 1;
}

enum WindingRule {
  NONZERO = 0;
  ODD = 1;
}

enum NodeType {
  NONE = 0;
  DOCUMENT = 1;
  CANVAS = 2;
  GROUP = 3;
  FRAME = 4;
  BOOLEAN_OPERATION = 5;
  VECTOR = 6;
  STAR = 7;
  LINE = 8;
  ELLIPSE = 9;
  RECTANGLE = 10;
  REGULAR_POLYGON = 11;
  ROUNDED_RECTANGLE = 12;
  TEXT = 13;
  SLICE = 14;
  SYMBOL = 15;
  INSTANCE = 16;
  STICKY = 17;
  SHAPE_WITH_TEXT = 18;
  CONNECTOR = 19;
  CODE_BLOCK = 20;
  WIDGET = 21;
  STAMP = 22;
  MEDIA = 23;
  HIGHLIGHT = 24;
  SECTION = 25;
  SECTION_OVERLAY = 26;
  WASHI_TAPE = 27;
  VARIABLE = 28;
  TABLE = 29;
  TABLE_CELL = 30;
  VARIABLE_SET = 31;
  SLIDE = 32;
  ASSISTED_LAYOUT = 33;
  INTERACTIVE_SLIDE_ELEMENT = 34;
  VARIABLE_OVERRIDE = 35;
  MODULE = 36;
  SLIDE_GRID = 37;
  SLIDE_ROW = 38;
  RESPONSIVE_SET = 39;
  CODE_COMPONENT = 40;
  TEXT_PATH = 41;
  CODE_INSTANCE = 42;
  CODE_LIBRARY = 43;
  CODE_FILE = 44;
  CODE_LAYER = 45;
  BRUSH = 46;
  MANAGED_STRING = 47;
  TRANSFORM = 48;
  CMS_RICH_TEXT = 49;
  REPEATER = 50;
  JSX = 51;
  EMBEDDED_PROTOTYPE = 52;
  REACT_FIBER = 53;
  RESPONSIVE_NODE_SET = 54;
  WEBPAGE = 55;
  KEYFRAME = 56;
  KEYFRAME_TRACK = 57;
  ANIMATION_PRESET_INSTANCE = 58;
  CODE_EMBED = 59;
  BINARY_FILE = 60;
  SPEC_BLOCK = 61;
  TOOL_INSTANCE = 62;
  CUSTOM_EFFECT_INSTANCE = 63;
  NATIVE_CODE_LAYER_INSTANCE = 64;
}

enum ShapeWithTextType {
  SQUARE = 0;
  ELLIPSE = 1;
  DIAMOND = 2;
  TRIANGLE_UP = 3;
  TRIANGLE_DOWN = 4;
  ROUNDED_RECTANGLE = 5;
  PARALLELOGRAM_RIGHT = 6;
  PARALLELOGRAM_LEFT = 7;
  ENG_DATABASE = 8;
  ENG_QUEUE = 9;
  ENG_FILE = 10;
  ENG_FOLDER = 11;
  TRAPEZOID = 12;
  PREDEFINED_PROCESS = 13;
  SHIELD = 14;
  DOCUMENT_SINGLE = 15;
  DOCUMENT_MULTIPLE = 16;
  MANUAL_INPUT = 17;
  HEXAGON = 18;
  CHEVRON = 19;
  PENTAGON = 20;
  OCTAGON = 21;
  STAR = 22;
  PLUS = 23;
  ARROW_LEFT = 24;
  ARROW_RIGHT = 25;
  SUMMING_JUNCTION = 26;
  OR = 27;
  SPEECH_BUBBLE = 28;
  INTERNAL_STORAGE = 29;
}

enum BlendMode {
  PASS_THROUGH = 0;
  NORMAL = 1;
  DARKEN = 2;
  MULTIPLY = 3;
  LINEAR_BURN = 4;
  COLOR_BURN = 5;
  LIGHTEN = 6;
  SCREEN = 7;
  LINEAR_DODGE = 8;
  COLOR_DODGE = 9;
  OVERLAY = 10;
  SOFT_LIGHT = 11;
  HARD_LIGHT = 12;
  DIFFERENCE = 13;
  EXCLUSION = 14;
  HUE = 15;
  SATURATION = 16;
  COLOR = 17;
  LUMINOSITY = 18;
}

enum PaintType {
  SOLID = 0;
  GRADIENT_LINEAR = 1;
  GRADIENT_RADIAL = 2;
  GRADIENT_ANGULAR = 3;
  GRADIENT_DIAMOND = 4;
  IMAGE = 5;
  EMOJI = 6;
  VIDEO = 7;
  PATTERN = 8;
  NOISE = 9;
  CUSTOM = 10;
}

enum ImageScaleMode {
  STRETCH = 0;
  FIT = 1;
  FILL = 2;
  TILE = 3;
}

enum EffectType {
  INNER_SHADOW = 0;
  DROP_SHADOW = 1;
  FOREGROUND_BLUR = 2;
  BACKGROUND_BLUR = 3;
  REPEAT = 4;
  SYMMETRY = 5;
  GRAIN = 6;
  NOISE = 7;
  GLASS = 8;
  CUSTOM = 9;
}

enum TextCase {
  ORIGINAL = 0;
  UPPER = 1;
  LOWER = 2;
  TITLE = 3;
  SMALL_CAPS = 4;
  SMALL_CAPS_FORCED = 5;
}

enum TextDecoration {
  NONE = 0;
  UNDERLINE = 1;
  STRIKETHROUGH = 2;
}

enum TextDecorationStyle {
  SOLID = 0;
  DOTTED = 1;
  WAVY = 2;
}

enum LeadingTrim {
  NONE = 0;
  CAP_HEIGHT = 1;
}

enum NumberUnits {
  RAW = 0;
  PIXELS = 1;
  PERCENT = 2;
}

enum ConstraintType {
  MIN = 0;
  CENTER = 1;
  MAX = 2;
  STRETCH = 3;
  SCALE = 4;
  FIXED_MIN = 5;
  FIXED_MAX = 6;
}

enum StrokeAlign {
  CENTER = 0;
  INSIDE = 1;
  OUTSIDE = 2;
  OFFSET = 3;
}

enum StrokeCap {
  NONE = 0;
  ROUND = 1;
  SQUARE = 2;
  ARROW_LINES = 3;
  ARROW_EQUILATERAL = 4;
  DIAMOND_FILLED = 5;
  TRIANGLE_FILLED = 6;
  HIGHLIGHT = 7;
  WASHI_TAPE_1 = 8;
  WASHI_TAPE_2 = 9;
  WASHI_TAPE_3 = 10;
  WASHI_TAPE_4 = 11;
  WASHI_TAPE_5 = 12;
  WASHI_TAPE_6 = 13;
  CIRCLE_FILLED = 14;
  ERD_ZERO_OR_ONE = 15;
  ERD_EXACTLY_ONE = 16;
  ERD_ZERO_OR_MORE = 17;
  ERD_ONE_OR_MORE = 18;
  ERD_ONE = 19;
  ERD_MANY = 20;
}

enum StrokeJoin {
  MITER = 0;
  BEVEL = 1;
  ROUND = 2;
}

enum BooleanOperation {
  UNION = 0;
  INTERSECT = 1;
  SUBTRACT = 2;
  XOR = 3;
}

enum TextAlignHorizontal {
  LEFT = 0;
  CENTER = 1;
  RIGHT = 2;
  JUSTIFIED = 3;
}

enum TextAlignVertical {
  TOP = 0;
  CENTER = 1;
  BOTTOM = 2;
}

enum MouseCursor {
  DEFAULT = 0;
  CROSSHAIR = 1;
  EYEDROPPER = 2;
  HAND = 3;
  PAINT_BUCKET = 4;
  PEN = 5;
  PENCIL = 6;
  MARKER = 7;
  ERASER = 8;
  HIGHLIGHTER = 9;
  LASSO = 10;
}

enum VectorMirror {
  NONE = 0;
  ANGLE = 1;
  ANGLE_AND_LENGTH = 2;
}

enum DashMode {
  CLIP = 0;
  STRETCH = 1;
}

enum ImageType {
  PNG = 0;
  JPEG = 1;
  SVG = 2;
  PDF = 3;
  MP4 = 4;
  GIF = 5;
}

enum ExportConstraintType {
  CONTENT_SCALE = 0;
  CONTENT_WIDTH = 1;
  CONTENT_HEIGHT = 2;
}

enum LayoutGridType {
  MIN = 0;
  CENTER = 1;
  STRETCH = 2;
  MAX = 3;
}

enum LayoutGridPattern {
  STRIPES = 0;
  GRID = 1;
}

enum TextAutoResize {
  NONE = 0;
  WIDTH_AND_HEIGHT = 1;
  HEIGHT = 2;
}

enum TextTruncation {
  DISABLED = 0;
  ENDING = 1;
}

enum StyleSetType {
  PERSONAL = 0;
  TEAM = 1;
  CUSTOM = 2;
  FREQUENCY = 3;
  TEMPORARY = 4;
}

enum StyleSetContentType {
  SOLID = 0;
  GRADIENT = 1;
  IMAGE = 2;
}

enum StackMode {
  NONE = 0;
  HORIZONTAL = 1;
  VERTICAL = 2;
  GRID = 3;
}

enum StackAlign {
  MIN = 0;
  CENTER = 1;
  MAX = 2;
  BASELINE = 3;
}

enum StackCounterAlign {
  MIN = 0;
  CENTER = 1;
  MAX = 2;
  STRETCH = 3;
  AUTO = 4;
  BASELINE = 5;
}

enum StackJustify {
  MIN = 0;
  CENTER = 1;
  MAX = 2;
  SPACE_EVENLY = 3;
  SPACE_BETWEEN = 4;
}

enum GridChildAlign {
  AUTO = 0;
  MIN = 1;
  CENTER = 2;
  MAX = 3;
}

enum GridAutoTracks {
  NONE = 0;
  ROWS = 1;
}

enum StackSize {
  FIXED = 0;
  RESIZE_TO_FIT = 1;
  RESIZE_TO_FIT_WITH_IMPLICIT_SIZE = 2;
}

enum StackPositioning {
  AUTO = 0;
  ABSOLUTE = 1;
}

enum StackWrap {
  NO_WRAP = 0;
  WRAP = 1;
}

enum StackCounterAlignContent {
  AUTO = 0;
  SPACE_BETWEEN = 1;
}

enum ConnectionType {
  NONE = 0;
  INTERNAL_NODE = 1;
  URL = 2;
  BACK = 3;
  CLOSE = 4;
  SET_VARIABLE = 5;
  UPDATE_MEDIA_RUNTIME = 6;
  CONDITIONAL = 7;
  SET_VARIABLE_MODE = 8;
  OBJECT_ANIMATION = 9;
  UPDATE_ANIMATION_TIMELINE_STATE = 10;
}

enum InteractionType {
  ON_CLICK = 0;
  AFTER_TIMEOUT = 1;
  MOUSE_IN = 2;
  MOUSE_OUT = 3;
  ON_HOVER = 4;
  MOUSE_DOWN = 5;
  MOUSE_UP = 6;
  ON_PRESS = 7;
  NONE = 8;
  DRAG = 9;
  ON_KEY_DOWN = 10;
  ON_VOICE = 11;
  ON_MEDIA_HIT = 12;
  ON_MEDIA_END = 13;
  MOUSE_ENTER = 14;
  MOUSE_LEAVE = 15;
}

enum TransitionType {
  INSTANT_TRANSITION = 0;
  DISSOLVE = 1;
  FADE = 2;
  SLIDE_FROM_LEFT = 3;
  SLIDE_FROM_RIGHT = 4;
  SLIDE_FROM_TOP = 5;
  SLIDE_FROM_BOTTOM = 6;
  PUSH_FROM_LEFT = 7;
  PUSH_FROM_RIGHT = 8;
  PUSH_FROM_TOP = 9;
  PUSH_FROM_BOTTOM = 10;
  MOVE_FROM_LEFT = 11;
  MOVE_FROM_RIGHT = 12;
  MOVE_FROM_TOP = 13;
  MOVE_FROM_BOTTOM = 14;
  SLIDE_OUT_TO_LEFT = 15;
  SLIDE_OUT_TO_RIGHT = 16;
  SLIDE_OUT_TO_TOP = 17;
  SLIDE_OUT_TO_BOTTOM = 18;
  MOVE_OUT_TO_LEFT = 19;
  MOVE_OUT_TO_RIGHT = 20;
  MOVE_OUT_TO_TOP = 21;
  MOVE_OUT_TO_BOTTOM = 22;
  MAGIC_MOVE = 23;
  SMART_ANIMATE = 24;
  SCROLL_ANIMATE = 25;
}

enum EasingType {
  IN_CUBIC = 0;
  OUT_CUBIC = 1;
  INOUT_CUBIC = 2;
  LINEAR = 3;
  IN_BACK_CUBIC = 4;
  OUT_BACK_CUBIC = 5;
  INOUT_BACK_CUBIC = 6;
  CUSTOM_CUBIC = 7;
  SPRING = 8;
  GENTLE_SPRING = 9;
  CUSTOM_SPRING = 10;
  SPRING_PRESET_ONE = 11;
  SPRING_PRESET_TWO = 12;
  SPRING_PRESET_THREE = 13;
  HOLD = 14;
}

enum ScrollDirection {
  NONE = 0;
  HORIZONTAL = 1;
  VERTICAL = 2;
  BOTH = 3;
}

enum ScrollContractedState {
  EXPANDED = 0;
  CONTRACTED = 1;
}

struct GUID {
  uint sessionID;
  uint localID;
}

struct Color {
  float r;
  float g;
  float b;
  float a;
}

struct Vector {
  float x;
  float y;
}

struct Rect {
  float x;
  float y;
  float w;
  float h;
}

struct ColorStop {
  Color color;
  float position;
}

message ColorStopVar {
  Color color = 1;
  VariableData colorVar = 2;
  float position = 3;
}

struct Matrix {
  float m00;
  float m01;
  float m02;
  float m10;
  float m11;
  float m12;
}

struct ParentIndex {
  GUID guid;
  string position;
}

struct Number {
  float value;
  NumberUnits units;
}

struct FontName {
  string family;
  string style;
  string postscript;
}

enum FontVariantNumericFigure {
  NORMAL = 0;
  LINING = 1;
  OLDSTYLE = 2;
}

enum FontVariantNumericSpacing {
  NORMAL = 0;
  PROPORTIONAL = 1;
  TABULAR = 2;
}

enum FontVariantNumericFraction {
  NORMAL = 0;
  DIAGONAL = 1;
  STACKED = 2;
}

enum FontVariantCaps {
  NORMAL = 0;
  SMALL = 1;
  ALL_SMALL = 2;
  PETITE = 3;
  ALL_PETITE = 4;
  UNICASE = 5;
  TITLING = 6;
}

enum FontVariantPosition {
  NORMAL = 0;
  SUB = 1;
  SUPER = 2;
}

enum FontStyle {
  NORMAL = 0;
  ITALIC = 1;
}

enum SemanticWeight {
  NORMAL = 0;
  BOLD = 1;
}

enum SemanticItalic {
  NORMAL = 0;
  ITALIC = 1;
}

enum CodeSnapshotState {
  INITIAL = 0;
  SNAPSHOTTING = 1;
  OK = 2;
  SNAPSHOT_ERROR = 3;
  LLM_IN_PROGRESS = 4;
}

enum SnapshotCaptureMode {
  FULL = 0;
  PARTIAL = 1;
}

message CodeSourceInfo {
  string originReferenceId = 1;
  GUID originNodeId = 2;
  GUID linkedSnapshotId = 3;
  SnapshotCaptureMode captureMode = 4;
  string sourceBlobRef = 5;
  string sourceElementId = 6;
}

enum CodeObjectType {
  WEB_LAYER = 0;
  WEB_INTERACTION = 1;
  NATIVE_LAYER = 2;
  ANIMATION_PRESET = 3;
  TOOL = 4;
  CUSTOM_EFFECT = 5;
  PLUGIN = 6;
  WEB_LAYER_GENERIC = 7;
  CUSTOM_FILL = 8;
}

message CustomToolArtifactRef {
  string customToolId = 1;
  string customToolVersion = 2;
  string publishedCustomToolId = 3;
  string publishedCustomToolVersionId = 4;
}

enum LockMode {
  NONE = 0;
  ALL = 1;
  BACKGROUND_ONLY = 2;
}

enum OpenTypeFeature {
  PCAP = 0;
  C2PC = 1;
  CASE = 2;
  CPSP = 3;
  TITL = 4;
  UNIC = 5;
  ZERO = 6;
  SINF = 7;
  ORDN = 8;
  AFRC = 9;
  DNOM = 10;
  NUMR = 11;
  LIGA = 12;
  CLIG = 13;
  DLIG = 14;
  HLIG = 15;
  RLIG = 16;
  AALT = 17;
  CALT = 18;
  RCLT = 19;
  SALT = 20;
  RVRN = 21;
  VERT = 22;
  SWSH = 23;
  CSWH = 24;
  NALT = 25;
  CCMP = 26;
  STCH = 27;
  HIST = 28;
  SIZE = 29;
  ORNM = 30;
  ITAL = 31;
  RAND = 32;
  DTLS = 33;
  FLAC = 34;
  MGRK = 35;
  SSTY = 36;
  KERN = 37;
  FWID = 38;
  HWID = 39;
  HALT = 40;
  TWID = 41;
  QWID = 42;
  PWID = 43;
  JUST = 44;
  LFBD = 45;
  OPBD = 46;
  RTBD = 47;
  PALT = 48;
  PKNA = 49;
  LTRA = 50;
  LTRM = 51;
  RTLA = 52;
  RTLM = 53;
  ABRV = 54;
  ABVM = 55;
  ABVS = 56;
  VALT = 57;
  VHAL = 58;
  BLWF = 59;
  BLWM = 60;
  BLWS = 61;
  AKHN = 62;
  CJCT = 63;
  CFAR = 64;
  CPCT = 65;
  CURS = 66;
  DIST = 67;
  EXPT = 68;
  FALT = 69;
  FINA = 70;
  FIN2 = 71;
  FIN3 = 72;
  HALF = 73;
  HALN = 74;
  HKNA = 75;
  HNGL = 76;
  HOJO = 77;
  INIT = 78;
  ISOL = 79;
  JP78 = 80;
  JP83 = 81;
  JP90 = 82;
  JP04 = 83;
  LJMO = 84;
  LOCL = 85;
  MARK = 86;
  MEDI = 87;
  MED2 = 88;
  MKMK = 89;
  NLCK = 90;
  NUKT = 91;
  PREF = 92;
  PRES = 93;
  VPAL = 94;
  PSTF = 95;
  PSTS = 96;
  RKRF = 97;
  RPHF = 98;
  RUBY = 99;
  SMPL = 100;
  TJMO = 101;
  TNAM = 102;
  TRAD = 103;
  VATU = 104;
  VJMO = 105;
  VKNA = 106;
  VKRN = 107;
  VRTR = 108;
  VRT2 = 109;
  SS01 = 110;
  SS02 = 111;
  SS03 = 112;
  SS04 = 113;
  SS05 = 114;
  SS06 = 115;
  SS07 = 116;
  SS08 = 117;
  SS09 = 118;
  SS10 = 119;
  SS11 = 120;
  SS12 = 121;
  SS13 = 122;
  SS14 = 123;
  SS15 = 124;
  SS16 = 125;
  SS17 = 126;
  SS18 = 127;
  SS19 = 128;
  SS20 = 129;
  CV01 = 130;
  CV02 = 131;
  CV03 = 132;
  CV04 = 133;
  CV05 = 134;
  CV06 = 135;
  CV07 = 136;
  CV08 = 137;
  CV09 = 138;
  CV10 = 139;
  CV11 = 140;
  CV12 = 141;
  CV13 = 142;
  CV14 = 143;
  CV15 = 144;
  CV16 = 145;
  CV17 = 146;
  CV18 = 147;
  CV19 = 148;
  CV20 = 149;
  CV21 = 150;
  CV22 = 151;
  CV23 = 152;
  CV24 = 153;
  CV25 = 154;
  CV26 = 155;
  CV27 = 156;
  CV28 = 157;
  CV29 = 158;
  CV30 = 159;
  CV31 = 160;
  CV32 = 161;
  CV33 = 162;
  CV34 = 163;
  CV35 = 164;
  CV36 = 165;
  CV37 = 166;
  CV38 = 167;
  CV39 = 168;
  CV40 = 169;
  CV41 = 170;
  CV42 = 171;
  CV43 = 172;
  CV44 = 173;
  CV45 = 174;
  CV46 = 175;
  CV47 = 176;
  CV48 = 177;
  CV49 = 178;
  CV50 = 179;
  CV51 = 180;
  CV52 = 181;
  CV53 = 182;
  CV54 = 183;
  CV55 = 184;
  CV56 = 185;
  CV57 = 186;
  CV58 = 187;
  CV59 = 188;
  CV60 = 189;
  CV61 = 190;
  CV62 = 191;
  CV63 = 192;
  CV64 = 193;
  CV65 = 194;
  CV66 = 195;
  CV67 = 196;
  CV68 = 197;
  CV69 = 198;
  CV70 = 199;
  CV71 = 200;
  CV72 = 201;
  CV73 = 202;
  CV74 = 203;
  CV75 = 204;
  CV76 = 205;
  CV77 = 206;
  CV78 = 207;
  CV79 = 208;
  CV80 = 209;
  CV81 = 210;
  CV82 = 211;
  CV83 = 212;
  CV84 = 213;
  CV85 = 214;
  CV86 = 215;
  CV87 = 216;
  CV88 = 217;
  CV89 = 218;
  CV90 = 219;
  CV91 = 220;
  CV92 = 221;
  CV93 = 222;
  CV94 = 223;
  CV95 = 224;
  CV96 = 225;
  CV97 = 226;
  CV98 = 227;
  CV99 = 228;
}

struct ExportConstraint {
  ExportConstraintType type;
  float value;
}

struct GUIDMapping {
  GUID from;
  GUID to;
}

struct Blob {
  byte[] bytes;
}

message Image {
  byte[] hash = 1;
  string name = 2;
  uint dataBlob = 3;
}

message Video {
  byte[] hash = 1;
  string s3Url = 2;
}

message PasteSource {
  string srcFile = 1;
  GUID srcNode = 2;
}

struct FilterColorAdjust {
  float tint;
  float shadows;
  float highlights;
  float detail;
  float exposure;
  float vignette;
  float temperature;
  float vibrance;
}

message PaintFilterMessage {
  float tint = 1;
  float shadows = 2;
  float highlights = 3;
  float detail = 4;
  float exposure = 5;
  float vignette = 6;
  float temperature = 7;
  float vibrance = 8;
  float contrast = 9;
  float brightness = 10;
}

message Paint {
  PaintType type = 1;
  Color color = 2;
  float opacity = 3;
  bool visible = 4;
  BlendMode blendMode = 5;
  ColorStop[] stops = 6;
  Matrix transform = 7;
  Image image = 8;
  Image imageThumbnail = 9;
  Image animatedImage = 16;
  uint animationFrame = 17;
  ImageScaleMode imageScaleMode = 10;
  bool imageShouldColorManage = 22;
  float rotation = 11;
  float scale = 12;
  FilterColorAdjust filterColorAdjust = 13;
  PaintFilterMessage paintFilter = 14;
  uint[] emojiCodePoints = 15;
  Video video = 18;
  uint originalImageWidth = 19;
  uint originalImageHeight = 20;
  VariableData opacityVar = 38;
  VariableData colorVar = 21;
  VariableData imageVar = 31;
  ColorStopVar[] stopsVar = 23;
  string thumbHashBase64 = 24;
  byte[] thumbHash = 25;
  GUID sourceNodeId = 26;
  float spacing = 27;
  Vector patternSpacing = 37;
  PatternTileType patternTileType = 28;
  PatternAlignment verticalAlignment = 29;
  PatternAlignment horizontalAlignment = 30;
  GUID id = 32;
  string altText = 33;
  NoiseType noiseType = 34;
  float density = 35;
  Vector noiseSize = 36;
  CodeComponentId customEffectId = 39;
  ComponentPropAssignment[] componentPropAssignments = 40;
}

enum NoiseType {
  MULTITONE = 0;
  MONOTONE = 1;
  DUOTONE = 2;
}

enum PatternTileType {
  RECTANGULAR = 0;
  HORIZONTAL_HEXAGONAL = 1;
  VERTICAL_HEXAGONAL = 2;
}

enum PatternAlignment {
  START = 0;
  CENTER = 1;
  END = 2;
}

message FontMetaData {
  FontName key = 1;
  float fontLineHeight = 2;
  byte[] fontDigest = 3;
  FontStyle fontStyle = 4;
  int fontWeight = 5;
}

message FontVariation {
  uint axisTag = 1;
  string axisName = 2;
  float value = 3;
}

message TextData {
  string characters = 1;
  uint[] characterStyleIDs = 2;
  NodeChange[] styleOverrideTable = 3;
  TextLineData[] lines = 12;
  uint layoutVersion = 8;
  FontName[] fallbackFonts = 10;
  float minContentHeight = 17;
  Vector layoutSize = 4;
  Baseline[] baselines = 5;
  Glyph[] glyphs = 6;
  Decoration[] decorations = 7;
  Blockquote[] blockquotes = 16;
  FontMetaData[] fontMetaData = 9;
  HyperlinkBox[] hyperlinkBoxes = 11;
  int truncationStartIndex = 13;
  float truncatedHeight = 14;
  float[] logicalIndexToCharacterOffsetMap = 15;
  MentionBox[] mentionBoxes = 18;
  DerivedTextLineData[] derivedLines = 19;
}

message DerivedTextData {
  Vector layoutSize = 1;
  Baseline[] baselines = 2;
  Glyph[] glyphs = 3;
  Decoration[] decorations = 4;
  Blockquote[] blockquotes = 5;
  FontMetaData[] fontMetaData = 6;
  HyperlinkBox[] hyperlinkBoxes = 7;
  int truncationStartIndex = 8;
  float truncatedHeight = 9;
  float[] logicalIndexToCharacterOffsetMap = 10;
  MentionBox[] mentionBoxes = 11;
  DerivedTextLineData[] derivedLines = 12;
}

message HyperlinkBox {
  Rect bounds = 1;
  string url = 2;
  GUID guid = 3;
  CMSItemPageTarget cmsTarget = 5;
  bool openInNewTab = 6;
  int hyperlinkID = 4;
}

message MentionBox {
  Rect bounds = 1;
  uint startIndex = 2;
  uint endIndex = 3;
  bool isValid = 4;
  uint mentionKey = 5;
}

message Baseline {
  Vector position = 1;
  float width = 2;
  float lineY = 3;
  float lineHeight = 4;
  float lineAscent = 7;
  float ignoreLeadingTrim = 8;
  uint firstCharacter = 5;
  uint endCharacter = 6;
}

message Glyph {
  uint commandsBlob = 1;
  Vector position = 2;
  uint styleID = 3;
  float fontSize = 4;
  uint firstCharacter = 5;
  float advance = 6;
  uint[] emojiCodePoints = 7;
  EmojiImageSet emojiImageSet = 8;
  float rotation = 9;
}

message Decoration {
  Rect[] rects = 1;
  uint styleID = 2;
}

message Blockquote {
  Rect verticalBar = 1;
  Rect quoteMarkBounds = 2;
  uint styleID = 3;
}

message VectorData {
  uint vectorNetworkBlob = 1;
  Vector normalizedSize = 2;
  NodeChange[] styleOverrideTable = 3;
}

message TextPathStart {
  float tValue = 1;
  bool forward = 2;
}

message GUIDPath {
  GUID[] guids = 1;
}

message SymbolData {
  GUID symbolID = 1;
  NodeChange[] symbolOverrides = 2;
  float uniformScaleFactor = 3;
}

message GUIDPathMapping {
  GUID id = 1;
  GUIDPath path = 2;
}

message DerivedBreakpointData {
  NodeChange[] overrides = 1;
}

message NodeGenerationData {
  NodeChange[] overrides = 1;
  bool useFineGrainedSyncing = 2;
  NodeChange[] diffOnlyRemovals = 3;
}

message DerivedImmutableFrameData {
  NodeChange[] overrides = 1;
  uint version = 2;
}

message JsxData {
  NodeChange[] overrides = 1;
}

message DerivedJsxData {
  NodeChange[] overrides = 1;
}

message AssetIdMap {
  AssetIdEntry[] entries = 1;
}

message AssetIdEntry {
  string assetKey = 1;
  AssetId assetId = 2;
}

message AssetRef {
  string key = 1;
  string version = 2;
}

message AssetId {
  GUID guid = 1;
  AssetRef assetRef = 2;
  StateGroupId stateGroupId = 3;
  StyleId styleId = 4;
  SymbolId symbolId = 5;
  VariableID variableId = 6;
  VariableSetID variableSetId = 7;
}

message StateGroupId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message StyleId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message SymbolId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message VariableID {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message VariableOverrideId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message VariableSetID {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message ModuleId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message ResponsiveSetId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message WebpageId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message ThemeID {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message CodeLibraryId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message CodeFileId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message CodeComponentId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message CanvasNodeId {
  GUID guid = 1;
  SymbolId symbolId = 2;
  StateGroupId stateGroupId = 3;
}

struct IndexRange {
  uint startIndex;
  uint endIndexExclusive;
}

struct CollaborativeTextOpID {
  uint sessionID;
  uint counterID;
}

enum CollaborativeTextOpType {
  INSERT = 0;
  DELETE = 1;
}

message CollaborativeTextStrippedOpRunWithIDs {
  CollaborativeTextOpID firstId = 1;
  uint runLength = 2;
  CollaborativeTextOpID[] parentIds = 3;
  CollaborativeTextOpID[] rebasedOnOpIds = 4;
}

message CollaborativeTextStrippedOpRunWithLoc {
  CollaborativeTextOpType type = 1;
  IndexRange range = 2;
  bool rangeShouldBeIteratedInReverse = 3;
  IndexRange contentBytesInBuffer = 4;
  IndexRange rebasedRange = 5;
}

message CollaborativeTextOpRun {
  CollaborativeTextOpID id = 1;
  CollaborativeTextOpID[] parentIds = 2;
  CollaborativeTextOpType type = 3;
  IndexRange range = 4;
  bool rangeShouldBeIteratedInReverse = 5;
  string content = 6;
  CollaborativeTextOpID[] rebasedOnOpIds = 7;
  IndexRange rebasedRange = 8;
}

message CollaborativePlainText {
  CollaborativeTextStrippedOpRunWithIDs[] historyOpsWithIds = 1;
  CollaborativeTextStrippedOpRunWithLoc[] historyOpsWithLoc = 2;
  byte[] historyStringContentBuffer = 3;
  CollaborativeTextOpRun[] changesToAppend = 4;
}

message CollaborativeTextSelection {
  GUID node = 1;
  uint field = 2;
  IndexRange selectedRange = 3;
  bool caretAtFront = 4;
  CollaborativeTextOpID[] textVersion = 5;
}

message ResponsiveTextStyleVariant {
  float minWidth = 1;
  NodeChange fields = 2;
  VariableData variableFontSize = 3;
  VariableData variableLineHeight = 4;
  VariableData variableLetterSpacing = 5;
  VariableData variableParagraphSpacing = 6;
  string name = 7;
}

enum FlappType {
  POLL = 0;
  EMBED = 1;
  FACEPILE = 2;
  ALIGNMENT = 3;
  YOUTUBE = 4;
}

message SlideThemeProps {
  string themeVersion = 1;
  VariableSetID variableSetId = 2;
  StyleId[] textStyleIds = 3;
  bool isTextColorManuallySelected = 4;
  bool isBorderColorManuallySelected = 5;
  AssetRef subscribedThemeRef = 6;
  uint schemaVersion = 7;
  bool isGeneratedFromDesign = 8;
}

message SlideThemeMap {
  SlideThemeMapEntry[] entries = 1;
}

message SlideThemeMapEntry {
  ThemeID themeId = 1;
  SlideThemeProps themeProps = 2;
}

message SharedSymbolReference {
  string fileKey = 1;
  GUID symbolID = 2;
  string versionHash = 3;
  GUIDPathMapping[] guidPathMappings = 4;
  byte[] bytes = 5;
  GUIDMapping[] libraryGUIDToSubscribingGUID = 6;
  string componentKey = 7;
  GUIDPathMapping[] unflatteningMappings = 8;
  bool isUnflattened = 9;
}

message SharedComponentMasterData {
  string componentKey = 1;
  GUIDPathMapping[] publishingGUIDPathToTeamLibraryGUID = 2;
  bool isUnflattened = 3;
}

message InstanceOverrideStash {
  GUIDPath overridePathOfSwappedInstance = 1;
  string componentKey = 2;
  NodeChange[] overrides = 3;
}

message InstanceOverrideStashV2 {
  GUIDPath overridePathOfSwappedInstance = 1;
  GUID localSymbolID = 2;
  NodeChange[] overrides = 3;
}

message ImportedCodeFileEntry {
  CodeFileId codeFileId = 1;
}

message ImportedCodeFiles {
  ImportedCodeFileEntry[] entries = 1;
}

enum BlurOpType {
  NORMAL = 0;
  PROGRESSIVE = 1;
}

enum RepeatType {
  LINEAR = 0;
  RADIAL = 1;
}

enum UnitType {
  PIXELS = 0;
  RELATIVE = 1;
}

enum RepeatOrder {
  FORWARD = 0;
  REVERSE = 1;
}

enum EffectAxis {
  X = 0;
  Y = 1;
  X_AND_Y = 2;
}

message Effect {
  EffectType type = 1;
  Vector offset = 3;
  float radius = 4;
  bool visible = 5;
  BlendMode blendMode = 6;
  float spread = 7;
  bool showShadowBehindNode = 8;
  VariableData radiusVar = 9;
  VariableData colorVar = 10;
  VariableData spreadVar = 11;
  VariableData xVar = 12;
  VariableData yVar = 13;
  uint count = 14;
  RepeatType repeatType = 15;
  EffectAxis axis = 16;
  UnitType unitType = 17;
  RepeatOrder order = 18;
  BlurOpType blurOpType = 19;
  Vector startOffset = 20;
  Vector endOffset = 28;
  float startRadius = 21;
  Color color = 2;
  Color secondaryColor = 24;
  Vector noiseSize = 22;
  uint seed = 29;
  bool clipToShape = 23;
  float density = 25;
  NoiseType noiseType = 26;
  float opacity = 27;
  float refractionRadius = 30;
  float specularAngle = 31;
  float specularIntensity = 32;
  float bevelSize = 33;
  float chromaticAberration = 34;
  float reflectionDistance = 35;
  float refractionIntensity = 36;
  VariableData refractionRadiusVar = 37;
  VariableData specularAngleVar = 38;
  VariableData specularIntensityVar = 39;
  VariableData chromaticAberrationVar = 40;
  VariableData splayVar = 41;
  VariableData refractionIntensityVar = 42;
  CodeComponentId customEffectId = 43;
  ComponentPropAssignment[] componentPropAssignments = 44;
  VariableData startRadiusVar = 45;
  VariableData startOffsetXVar = 46;
  VariableData startOffsetYVar = 47;
  VariableData endOffsetXVar = 48;
  VariableData endOffsetYVar = 49;
  VariableData noiseSizeXVar = 50;
  VariableData noiseSizeYVar = 51;
  VariableData densityVar = 52;
  VariableData effectOpacityVar = 53;
  VariableData secondaryColorVar = 54;
  GUID id = 55;
}

enum TransformModifierType {
  REPEAT = 0;
  SYMMETRY = 1;
  SKEW = 2;
}

message TransformModifier {
  TransformModifierType type = 1;
  Vector offset = 2;
  bool visible = 3;
  uint count = 4;
  RepeatType repeatType = 5;
  EffectAxis axis = 6;
  UnitType unitType = 7;
  RepeatOrder order = 8;
  float skewX = 9;
  float skewY = 10;
}

enum TransformSchemaType {
  NONE = 0;
  FIXED_ORDER = 1;
  FREEFORM = 2;
}

struct Matrix4f {
  float m00;
  float m01;
  float m02;
  float m03;
  float m10;
  float m11;
  float m12;
  float m13;
  float m20;
  float m21;
  float m22;
  float m23;
  float m30;
  float m31;
  float m32;
  float m33;
}

message FixedOrderTransform3d {
  float perspective = 1;
  float translateZ = 2;
  float rotateX = 3;
  float rotateY = 4;
  float rotateZ = 5;
}

enum TransformFnType {
  NONE = 0;
  MATRIX_3D = 1;
  PERSPECTIVE = 2;
  TRANSLATE_Z = 3;
  ROTATE_X = 4;
  ROTATE_Y = 5;
  ROTATE_Z = 6;
}

message TransformFnValue {
  Matrix4f matrix3d = 1;
  float perspective = 2;
  float rotateAngle = 3;
  float translateValue = 4;
}

message TransformFn {
  TransformFnType type = 1;
  TransformFnValue value = 2;
}

message TransformSchemaValue {
  FixedOrderTransform3d fixedOrderTransform3d = 1;
  TransformFn[] transformFunctions = 2;
}

message Transform3d {
  TransformSchemaType type = 1;
  TransformSchemaValue value = 2;
  bool backfaceHidden = 3;
}

struct NumberVector2D {
  Number x;
  Number y;
}

message Scene3d {
  float perspective = 1;
  NumberVector2D perspectiveOrigin = 2;
  bool preserve3d = 3;
}

message TransformOrigin {
  Number x = 1;
  Number y = 2;
}

message TransitionInfo {
  TransitionType type = 1;
  float duration = 2;
}

enum PrototypeDeviceType {
  NONE = 0;
  PRESET = 1;
  CUSTOM = 2;
  PRESENTATION = 3;
}

enum DeviceRotation {
  NONE = 0;
  CCW_90 = 1;
}

message PrototypeDevice {
  PrototypeDeviceType type = 1;
  Vector size = 2;
  string presetIdentifier = 3;
  DeviceRotation rotation = 4;
}

enum OverlayPositionType {
  CENTER = 0;
  TOP_LEFT = 1;
  TOP_CENTER = 2;
  TOP_RIGHT = 3;
  BOTTOM_LEFT = 4;
  BOTTOM_CENTER = 5;
  BOTTOM_RIGHT = 6;
  MANUAL = 7;
}

enum OverlayBackgroundInteraction {
  NONE = 0;
  CLOSE_ON_CLICK_OUTSIDE = 1;
}

enum OverlayBackgroundType {
  NONE = 0;
  SOLID_COLOR = 1;
}

message OverlayBackgroundAppearance {
  OverlayBackgroundType backgroundType = 1;
  Color backgroundColor = 2;
}

enum NavigationType {
  NAVIGATE = 0;
  OVERLAY = 1;
  SWAP = 2;
  SWAP_STATE = 3;
  SCROLL_TO = 4;
}

enum ExportColorProfile {
  DOCUMENT = 0;
  SRGB = 1;
  DISPLAY_P3_V4 = 2;
  CMYK = 3;
}

enum ExportBackgroundType {
  SOLID = 0;
  TRANSPARENT = 1;
  GRID = 2;
}

message ExportSettings {
  string suffix = 1;
  ImageType imageType = 2;
  ExportConstraint constraint = 3;
  bool svgDataName = 4;
  ExportSVGIDMode svgIDMode = 5;
  bool svgOutlineText = 6;
  bool contentsOnly = 7;
  bool svgForceStrokeMasks = 8;
  bool useAbsoluteBounds = 9;
  ExportColorProfile colorProfile = 10;
  float quality = 11;
  bool useBicubicSampler = 12;
  int frameRate = 13;
  int loopCount = 14;
  ExportBackgroundType backgroundType = 15;
}

enum ExportSVGIDMode {
  IF_NEEDED = 0;
  ALWAYS = 1;
}

message LayoutGrid {
  LayoutGridType type = 1;
  Axis axis = 2;
  bool visible = 3;
  int numSections = 4;
  float offset = 5;
  float sectionSize = 6;
  float gutterSize = 7;
  Color color = 8;
  LayoutGridPattern pattern = 9;
  VariableData numSectionsVar = 10;
  VariableData offsetVar = 11;
  VariableData sectionSizeVar = 12;
  VariableData gutterSizeVar = 13;
}

message Guide {
  Axis axis = 1;
  float offset = 2;
  GUID guid = 3;
}

message Path {
  WindingRule windingRule = 1;
  uint commandsBlob = 2;
  uint styleID = 3;
}

enum StyleType {
  NONE = 0;
  FILL = 1;
  STROKE = 2;
  TEXT = 3;
  EFFECT = 4;
  EXPORT = 5;
  GRID = 6;
  ANIMATION = 7;
}

enum BrushOrientation {
  FORWARD = 0;
  REVERSE = 1;
}

enum BrushType {
  STRETCH = 0;
  SCATTER = 1;
}

message DynamicStrokeSettings {
  float frequency = 1;
  float wiggle = 2;
  float smoothen = 3;
}

message ScatterStrokeSettings {
  float gap = 1;
  float wiggle = 2;
  float angularJitter = 3;
  float rotation = 4;
  float sizeJitter = 5;
}

message StretchStrokeSettings {
  BrushOrientation orientation = 1;
}

message StrokeData {
  Stroke[] strokes = 1;
  uint version = 2;
}

message Stroke {
  int strokeId = 1;
  float strokeWeight = 2;
  VariableData strokeWeightVar = 3;
  Paint[] strokePaint = 4;
  StyleId styleIdForStrokeFill = 5;
  StrokeAlign strokeAlign = 6;
  StrokeCap strokeCap = 7;
  Number strokeCapSize = 8;
  StrokeJoin strokeJoin = 9;
  float miterLimit = 10;
  float[] dashPattern = 11;
  OptionalVector pathTrim = 12;
  float strokeOffset = 13;
  bool isDeleted = 14;
}

message VariableWidthPoint {
  float position = 1;
  float ascent = 2;
  float descent = 3;
  int segmentId = 4;
}

message SharedStyleReference {
  string styleKey = 1;
  string versionHash = 2;
}

message SharedStyleMasterData {
  string styleKey = 1;
  string sortPosition = 2;
  string fileKey = 3;
}

enum ScrollBehavior {
  SCROLLS = 0;
  FIXED_WHEN_CHILD_OF_SCROLLING_FRAME = 1;
  STICKY_SCROLLS = 2;
}

message ArcData {
  float startingAngle = 1;
  float endingAngle = 2;
  float innerRadius = 3;
}

message SymbolLink {
  string uri = 1;
  string displayName = 2;
  string displayText = 3;
}

message PluginData {
  string pluginID = 1;
  string value = 2;
  string key = 3;
}

message PluginRelaunchData {
  string pluginID = 1;
  string message = 2;
  string command = 3;
  bool isDeleted = 4;
  CodeComponentId customToolId = 5;
}

message MultiplayerFieldVersion {
  uint counter = 1;
  uint sessionID = 2;
}

enum ConnectorMagnet {
  NONE = 0;
  AUTO = 1;
  TOP = 2;
  LEFT = 3;
  BOTTOM = 4;
  RIGHT = 5;
  CENTER = 6;
  AUTO_HORIZONTAL = 7;
  EDGE = 8;
  ABSOLUTE = 9;
}

message ConnectorEndpoint {
  GUID endpointNodeID = 1;
  Vector position = 2;
  ConnectorMagnet magnet = 3;
  Vector relativePosition = 4;
}

message ConnectorControlPoint {
  Vector position = 1;
  Vector axis = 2;
}

enum ConnectorTextSection {
  MIDDLE_TO_START = 0;
  MIDDLE_TO_END = 1;
}

enum ConnectorOffAxisOffset {
  NONE = 0;
  ABOVE = 1;
  BELOW = 2;
}

message ConnectorTextMidpoint {
  ConnectorTextSection section = 1;
  float offset = 2;
  ConnectorOffAxisOffset offAxisOffset = 3;
}

enum ConnectorLineStyle {
  ELBOWED = 0;
  STRAIGHT = 1;
  CURVED = 2;
}

enum ConnectorType {
  MANUAL = 0;
  DIAGRAM = 1;
}

enum AnnotationPropertyType {
  FILL = 0;
  STROKE = 1;
  WIDTH = 2;
  HEIGHT = 3;
  MIN_WIDTH = 4;
  MIN_HEIGHT = 5;
  MAX_WIDTH = 6;
  MAX_HEIGHT = 7;
  STROKE_WIDTH = 8;
  CORNER_RADIUS = 9;
  EFFECT = 10;
  TEXT_STYLE = 11;
  TEXT_ALIGN_HORIZONTAL = 12;
  FONT_FAMILY = 13;
  FONT_SIZE = 14;
  FONT_WEIGHT = 15;
  LINE_HEIGHT = 16;
  LETTER_SPACING = 17;
  STACK_SPACING = 18;
  STACK_PADDING = 19;
  STACK_MODE = 20;
  STACK_ALIGNMENT = 21;
  OPACITY = 22;
  COMPONENT = 23;
  FONT_STYLE = 24;
  GRID_ROW_GAP = 25;
  GRID_COLUMN_GAP = 26;
  GRID_ROW_COUNT = 27;
  GRID_COLUMN_COUNT = 28;
  GRID_ROW_ANCHOR_INDEX = 29;
  GRID_COLUMN_ANCHOR_INDEX = 30;
  GRID_ROW_SPAN = 31;
  GRID_COLUMN_SPAN = 32;
}

message AnnotationProperty {
  AnnotationPropertyType type = 1;
}

enum AnnotationCategoryPreset {
  NONE = 0;
  ACCESSIBILITY = 1;
  BEHAVIOR = 2;
  CONTENT = 3;
  DEVELOPMENT = 4;
  INTERACTION = 5;
}

enum AnnotationCategoryColor {
  YELLOW = 0;
  ORANGE = 1;
  RED = 2;
  PINK = 3;
  VIOLET = 4;
  BLUE = 5;
  TEAL = 6;
  GREEN = 7;
}

message AnnotationCategoryCustom {
  AnnotationCategoryColor color = 1;
  Color customColor = 2;
  string label = 3;
}

message AnnotationCategory {
  GUID id = 1;
  AnnotationCategoryPreset preset = 2;
  AnnotationCategoryCustom custom = 3;
}

message AnnotationCategories {
  uint version = 1;
  AnnotationCategory[] items = 2;
}

message Annotation {
  string label = 1;
  AnnotationProperty[] properties = 2;
  string labelV2 = 3;
  GUID categoryId = 4;
}

enum AnnotationMeasurementNodeSide {
  TOP = 0;
  BOTTOM = 1;
  LEFT = 2;
  RIGHT = 3;
}

message AnnotationMeasurement {
  GUID id = 1;
  GUID fromNode = 2;
  GUID toNode = 3;
  AnnotationMeasurementNodeSide fromNodeSide = 4;
  bool toSameSide = 5;
  float innerOffsetRelative = 6;
  float outerOffsetFixed = 7;
  GUIDPath toNodeStablePath = 8;
  string freeText = 9;
}

message LibraryMoveInfo {
  string oldKey = 1;
  string pasteFileKey = 2;
}

message LibraryMoveHistoryItem {
  GUID sourceNodeId = 1;
  string sourceComponentKey = 2;
}

message DeveloperRelatedLink {
  string nodeId = 1;
  string fileKey = 2;
  string linkName = 3;
  string linkUrl = 4;
}

message WidgetPointer {
  GUID nodeId = 1;
}

message EditInfo {
  string timestampIso8601 = 1;
  string userId = 2;
  uint lastEditedAt = 3;
  uint createdAt = 4;
}

enum EditorType {
  DESIGN = 0;
  WHITEBOARD = 1;
  SLIDES = 2;
  DEV_HANDOFF = 3;
  SITES = 4;
  COOPER = 5;
  ILLUSTRATION = 6;
  FIGMAKE = 7;
  FIGSPEC = 8;
}

enum MaskType {
  ALPHA = 0;
  OUTLINE = 1;
  LUMINANCE = 2;
}

enum ModuleType {
  NONE = 0;
  SINGLE_NODE = 1;
  MULTI_NODE = 2;
}

enum SectionStatus {
  NONE = 0;
  BUILD = 1;
  COMPLETED = 2;
}

message SectionStatusInfo {
  SectionStatus status = 1;
  uint lastUpdateUnixTimestamp = 2;
  string description = 3;
  string userId = 4;
  SectionStatus prevStatus = 5;
}

message BuzzApprovalRequestInfo {
  string requestId = 1;
  string requesterUserId = 2;
  uint requestedAt = 3;
  string[] reviewerUserIds = 4;
  string title = 5;
  string note = 6;
  GUID[] assetsInRequest = 7;
}

message BuzzApprovalRequests {
  BuzzApprovalRequestInfo[] requests = 1;
}

enum BuzzApprovalNodeStatus {
  NONE = 0;
  IN_REVIEW = 1;
  APPROVED = 2;
  CHANGES_REQUESTED = 3;
}

message BuzzApprovalNodeStatusInfo {
  BuzzApprovalNodeStatus currentStatus = 1;
  bool wasPreviouslyApproved = 2;
  uint[] approvalRevokedAtHistory = 3;
}

message CodeEmbedInfo {
  string url = 1;
  string srcUrl = 2;
  string title = 3;
  string thumbnailImageHash = 4;
  bool isPublishedSite = 5;
}

enum VariableTimingDisplayUnit {
  MILLISECONDS = 0;
  SECONDS = 1;
}

message NodeChange {
  GUID guid = 1;
  uint guidTag = 53;
  NodePhase phase = 2;
  uint phaseTag = 54;
  ParentIndex parentIndex = 3;
  uint parentIndexTag = 55;
  NodeType type = 4;
  uint typeTag = 56;
  string name = 5;
  uint nameTag = 57;
  bool isPublishable = 174;
  string description = 318;
  LibraryMoveInfo libraryMoveInfo = 256;
  LibraryMoveHistoryItem[] libraryMoveHistory = 281;
  string key = 319;
  AssetIdMap fileAssetIds = 383;
  uint styleID = 49;
  uint styleIDTag = 101;
  bool isFillStyle = 157;
  bool isStrokeStyle = 161;
  bool isOverrideOverTextStyle = 376;
  StyleType styleType = 163;
  string styleDescription = 191;
  string version = 171;
  string userFacingVersion = 399;
  string sortPosition = 320;
  SharedStyleMasterData ojansSuperSecretNodeField = 345;
  SharedStyleMasterData sevMoonlitLilyData = 348;
  bool isSoftDeletedStyle = 176;
  bool isNonUpdateable = 177;
  SharedStyleMasterData sharedStyleMasterData = 172;
  SharedStyleReference sharedStyleReference = 173;
  GUID inheritFillStyleID = 158;
  GUID inheritStrokeStyleID = 162;
  GUID inheritTextStyleID = 167;
  GUID inheritExportStyleID = 168;
  GUID inheritEffectStyleID = 169;
  GUID inheritGridStyleID = 170;
  GUID inheritFillStyleIDForStroke = 185;
  StyleId styleIdForFill = 332;
  StyleId styleIdForStrokeFill = 333;
  StyleId styleIdForText = 334;
  StyleId styleIdForEffect = 335;
  StyleId styleIdForGrid = 336;
  StyleAnimation[] styleAnimations = 580;
  Paint[] backgroundPaints = 193;
  GUID inheritFillStyleIDForBackground = 194;
  bool isStateGroup = 225;
  StateGroupPropertyValueOrder[] stateGroupPropertyValueOrders = 238;
  PartialPasteAnnotation partialPasteAnnotation = 581;
  SharedSymbolReference sharedSymbolReference = 122;
  bool isSymbolPublishable = 123;
  GUIDPathMapping[] sharedSymbolMappings = 124;
  string sharedSymbolVersion = 126;
  SharedComponentMasterData sharedComponentMasterData = 152;
  string symbolDescription = 144;
  GUIDPathMapping[] unflatteningMappings = 164;
  GUIDPathMapping[] forceUnflatteningMappings = 228;
  string publishFile = 214;
  string sourceLibraryKey = 395;
  GUID publishID = 215;
  string componentKey = 216;
  bool isC2 = 217;
  string publishedVersion = 218;
  string originComponentKey = 252;
  ComponentPropDef[] componentPropDefs = 266;
  ComponentPropRef[] componentPropRefs = 267;
  VariantPropSpec[] variantPropSpecs = 483;
  SymbolData symbolData = 113;
  uint symbolDataTag = 114;
  NodeChange[] derivedSymbolData = 125;
  bool nestedInstanceResizeEnabled = 394;
  GUID overriddenSymbolID = 143;
  ComponentPropAssignment[] componentPropAssignments = 268;
  bool propsAreBubbled = 305;
  InstanceOverrideStash[] overrideStash = 248;
  InstanceOverrideStashV2[] overrideStashV2 = 250;
  GUIDPath guidPath = 111;
  uint guidPathTag = 112;
  int overrideLevel = 321;
  ModuleType moduleType = 382;
  bool isSlot = 463;
  bool isSlotContent = 495;
  float fontSize = 21;
  uint fontSizeTag = 73;
  float paragraphIndent = 22;
  uint paragraphIndentTag = 74;
  float paragraphSpacing = 23;
  uint paragraphSpacingTag = 75;
  TextAlignHorizontal textAlignHorizontal = 32;
  uint textAlignHorizontalTag = 84;
  TextAlignVertical textAlignVertical = 33;
  uint textAlignVerticalTag = 85;
  TextCase textCase = 34;
  uint textCaseTag = 86;
  TextDecoration textDecoration = 35;
  uint textDecorationTag = 87;
  Number lineHeight = 40;
  uint lineHeightTag = 92;
  FontName fontName = 41;
  uint fontNameTag = 93;
  TextData textData = 42;
  uint textDataTag = 94;
  DerivedTextData derivedTextData = 359;
  bool fontVariantCommonLigatures = 127;
  bool fontVariantContextualLigatures = 128;
  bool fontVariantDiscretionaryLigatures = 129;
  bool fontVariantHistoricalLigatures = 130;
  bool fontVariantOrdinal = 131;
  bool fontVariantSlashedZero = 132;
  FontVariantNumericFigure fontVariantNumericFigure = 133;
  FontVariantNumericSpacing fontVariantNumericSpacing = 134;
  FontVariantNumericFraction fontVariantNumericFraction = 135;
  FontVariantCaps fontVariantCaps = 136;
  FontVariantPosition fontVariantPosition = 137;
  Number letterSpacing = 165;
  string fontVersion = 202;
  LeadingTrim leadingTrim = 322;
  bool hangingPunctuation = 337;
  bool hangingList = 339;
  bool fallbackGlyphs = 550;
  int maxLines = 351;
  ResponsiveTextStyleVariant[] responsiveTextStyleVariants = 417;
  SectionStatus sectionStatus = 352;
  SectionStatusInfo sectionStatusInfo = 355;
  uint textUserLayoutVersion = 203;
  uint textExplicitLayoutVersion = 396;
  OpenTypeFeature[] toggledOnOTFeatures = 205;
  OpenTypeFeature[] toggledOffOTFeatures = 206;
  Hyperlink hyperlink = 223;
  Mention mention = 340;
  FontVariation[] fontVariations = 260;
  uint textBidiVersion = 279;
  TextTruncation textTruncation = 280;
  bool hasHadRTLText = 292;
  EmojiImageSet emojiImageSet = 391;
  string slideThumbnailHash = 392;
  bool visible = 6;
  uint visibleTag = 58;
  bool locked = 7;
  uint lockedTag = 59;
  LockMode lockMode = 434;
  float opacity = 8;
  uint opacityTag = 60;
  BlendMode blendMode = 9;
  uint blendModeTag = 61;
  Vector size = 11;
  uint sizeTag = 63;
  Matrix transform = 12;
  uint transformTag = 64;
  float[] dashPattern = 13;
  uint dashPatternTag = 65;
  bool mask = 16;
  uint maskTag = 68;
  Vector rotationOrigin = 424;
  bool maskIsOutline = 18;
  uint maskIsOutlineTag = 70;
  MaskType maskType = 317;
  float backgroundOpacity = 19;
  uint backgroundOpacityTag = 71;
  float cornerRadius = 20;
  uint cornerRadiusTag = 72;
  float strokeWeight = 26;
  uint strokeWeightTag = 78;
  StrokeAlign strokeAlign = 29;
  uint strokeAlignTag = 81;
  StrokeCap strokeCap = 30;
  uint strokeCapTag = 82;
  Number strokeCapSize = 497;
  StrokeJoin strokeJoin = 31;
  uint strokeJoinTag = 83;
  Paint[] fillPaints = 38;
  uint fillPaintsTag = 90;
  Paint[] strokePaints = 39;
  uint strokePaintsTag = 91;
  Effect[] effects = 43;
  uint effectsTag = 95;
  Color backgroundColor = 50;
  uint backgroundColorTag = 102;
  Path[] fillGeometry = 51;
  uint fillGeometryTag = 103;
  Path[] strokeGeometry = 52;
  uint strokeGeometryTag = 104;
  Path[] offsetFillMaskGeometry = 564;
  Paint[] textDecorationFillPaints = 411;
  bool textDecorationSkipInk = 412;
  Number textUnderlineOffset = 413;
  Number textDecorationThickness = 415;
  TextDecorationStyle textDecorationStyle = 416;
  TransformModifier[] transformModifiers = 455;
  Transform3d transform3d = 570;
  StrokeData strokeData = 571;
  Scene3d scene3d = 572;
  TransformOrigin transformOrigin = 587;
  float rectangleTopLeftCornerRadius = 145;
  float rectangleTopRightCornerRadius = 146;
  float rectangleBottomLeftCornerRadius = 147;
  float rectangleBottomRightCornerRadius = 148;
  bool rectangleCornerRadiiIndependent = 149;
  bool rectangleCornerToolIndependent = 150;
  bool proportionsConstrained = 151;
  OptionalVector targetAspectRatio = 423;
  bool useAbsoluteBounds = 258;
  bool borderTopHidden = 287;
  bool borderBottomHidden = 288;
  bool borderLeftHidden = 289;
  bool borderRightHidden = 290;
  bool bordersTakeSpace = 294;
  float borderTopWeight = 295;
  float borderBottomWeight = 296;
  float borderLeftWeight = 297;
  float borderRightWeight = 298;
  bool borderStrokeWeightsIndependent = 299;
  ConstraintType horizontalConstraint = 28;
  uint horizontalConstraintTag = 80;
  StackMode stackMode = 105;
  uint stackModeTag = 106;
  float stackSpacing = 107;
  uint stackSpacingTag = 108;
  float stackPadding = 109;
  uint stackPaddingTag = 110;
  StackCounterAlign stackCounterAlign = 120;
  StackJustify stackJustify = 121;
  StackAlign stackAlign = 208;
  float stackHorizontalPadding = 209;
  float stackVerticalPadding = 210;
  StackSize stackWidth = 211;
  StackSize stackHeight = 212;
  StackSize stackPrimarySizing = 229;
  StackJustify stackPrimaryAlignItems = 230;
  StackAlign stackCounterAlignItems = 231;
  float stackChildPrimaryGrow = 232;
  float stackPaddingRight = 233;
  float stackPaddingBottom = 234;
  StackCounterAlign stackChildAlignSelf = 236;
  StackPositioning stackPositioning = 269;
  bool stackReverseZIndex = 271;
  StackWrap stackWrap = 323;
  float stackCounterSpacing = 324;
  OptionalVector minSize = 325;
  OptionalVector maxSize = 326;
  StackCounterAlignContent stackCounterAlignContent = 343;
  int[] sortedMovingChildIndices = 406;
  uint stackLayoutVersion = 574;
  GUIDPositionMap gridRows = 435;
  GUIDPositionMap gridColumns = 436;
  float gridRowGap = 437;
  float gridColumnGap = 438;
  GUID gridRowAnchor = 439;
  GUID gridColumnAnchor = 440;
  uint gridRowSpan = 441;
  uint gridColumnSpan = 442;
  GUIDGridTrackSizeMap gridColumnsSizing = 474;
  GUIDGridTrackSizeMap gridRowsSizing = 475;
  GridChildAlign gridChildVerticalAlign = 476;
  GridChildAlign gridChildHorizontalAlign = 477;
  GridAutoTracks gridAutoTracks = 555;
  bool gridReflowEnabled = 556;
  bool isSnakeGameBoard = 344;
  GUID transitionNodeID = 139;
  GUID prototypeStartNodeID = 140;
  Color prototypeBackgroundColor = 141;
  TransitionInfo transitionInfo = 153;
  TransitionType transitionType = 154;
  float transitionDuration = 155;
  EasingType easingType = 156;
  bool transitionPreserveScroll = 181;
  ConnectionType connectionType = 182;
  string connectionURL = 183;
  PrototypeDevice prototypeDevice = 184;
  InteractionType interactionType = 187;
  float transitionTimeout = 188;
  bool interactionMaintained = 189;
  float interactionDuration = 190;
  bool destinationIsOverlay = 192;
  bool transitionShouldSmartAnimate = 207;
  PrototypeInteraction[] prototypeInteractions = 226;
  PrototypeInteraction[] objectAnimations = 426;
  PrototypeStartingPoint prototypeStartingPoint = 249;
  PluginData[] pluginData = 204;
  PluginRelaunchData[] pluginRelaunchData = 219;
  ConnectorEndpoint connectorStart = 242;
  ConnectorEndpoint connectorEnd = 243;
  ConnectorLineStyle connectorLineStyle = 244;
  StrokeCap connectorStartCap = 245;
  StrokeCap connectorEndCap = 246;
  ConnectorControlPoint[] connectorControlPoints = 253;
  ConnectorControlPoint[] connectorBezierControlPoints = 479;
  ConnectorTextMidpoint connectorTextMidpoint = 255;
  ConnectorType connectorType = 373;
  int connectorVersion = 533;
  Annotation[] annotations = 369;
  AnnotationMeasurement[] measurements = 384;
  AnnotationCategories annotationCategories = 453;
  ShapeWithTextType shapeWithTextType = 241;
  float shapeUserHeight = 247;
  bool isStrokePaintDerived = 530;
  DerivedImmutableFrameData derivedImmutableFrameData = 254;
  MultiplayerFieldVersion derivedImmutableFrameDataVersion = 338;
  NodeGenerationData nodeGenerationData = 240;
  JsxData jsxData = 491;
  DerivedJsxData derivedJsxData = 492;
  string stableKey = 493;
  CodeBlockLanguage codeBlockLanguage = 259;
  CodeBlockTheme codeBlockTheme = 433;
  LinkPreviewData linkPreviewData = 278;
  bool shapeTruncates = 282;
  bool sectionContentsHidden = 283;
  VideoPlayback videoPlayback = 300;
  StampData stampData = 301;
  SectionPresetInfo sectionPresetInfo = 370;
  PlatformShapeDefinition platformShapeDefinition = 409;
  MultiplayerMap widgetSyncedState = 273;
  uint widgetSyncCursor = 274;
  WidgetDerivedSubtreeCursor widgetDerivedSubtreeCursor = 275;
  WidgetPointer widgetCachedAncestor = 276;
  WidgetInputBehavior widgetInputBehavior = 285;
  string widgetTooltip = 286;
  WidgetHoverStyle widgetHoverStyle = 291;
  bool isWidgetStickable = 293;
  bool shouldHideCursorsOnWidgetHover = 360;
  WidgetMetadata widgetMetadata = 262;
  WidgetEvent[] widgetEvents = 263;
  WidgetPropertyMenuItem[] widgetPropertyMenuItems = 265;
  WidgetInputTextNodeType widgetInputTextNodeType = 401;
  MultiplayerMap jsxProps = 489;
  TableRowColumnPositionMap tableRowPositions = 308;
  TableRowColumnPositionMap tableColumnPositions = 309;
  TableRowColumnSizeMap tableRowHeights = 310;
  TableRowColumnSizeMap tableColumnWidths = 311;
  TableMergedCellMap tableMergedCells = 538;
  MultiplayerMap interactiveSlideConfigData = 371;
  MultiplayerMap interactiveSlideParticipantData = 372;
  FlappType flappType = 402;
  bool isEmbeddedPrototype = 486;
  string slideSpeakerNotes = 389;
  bool isSkippedSlide = 410;
  MultiplayerMap presentationOutlines = 573;
  ThemeID themeID = 379;
  SlideThemeData slideThemeData = 381;
  SlideThemeMap slideThemeMap = 390;
  string slideTemplateFileKey = 393;
  SlideNumber slideNumber = 443;
  string slideNumberSeparator = 456;
  GUID diagramParentId = 363;
  GUID layoutRoot = 362;
  string layoutPosition = 364;
  DiagramLayoutRuleType diagramLayoutRuleType = 366;
  DiagramParentIndex diagramParentIndex = 367;
  DiagramLayoutPaused diagramLayoutPaused = 368;
  bool isPageDivider = 380;
  InternalEnumForTest internalEnumForTest = 251;
  InternalDataForTest internalDataForTest = 257;
  bool autoRename = 14;
  uint autoRenameTag = 66;
  bool backgroundEnabled = 15;
  uint backgroundEnabledTag = 67;
  bool exportContentsOnly = 17;
  uint exportContentsOnlyTag = 69;
  float miterLimit = 25;
  uint miterLimitTag = 77;
  float textTracking = 27;
  uint textTrackingTag = 79;
  ConstraintType verticalConstraint = 37;
  uint verticalConstraintTag = 89;
  ExportSettings[] exportSettings = 45;
  uint exportSettingsTag = 97;
  TextAutoResize textAutoResize = 46;
  uint textAutoResizeTag = 98;
  LayoutGrid[] layoutGrids = 47;
  uint layoutGridsTag = 99;
  bool frameMaskDisabled = 115;
  uint frameMaskDisabledTag = 116;
  bool resizeToFit = 117;
  uint resizeToFitTag = 118;
  BooleanOperation booleanOperation = 36;
  uint booleanOperationTag = 88;
  VectorMirror handleMirroring = 44;
  uint handleMirroringTag = 96;
  uint count = 10;
  uint countTag = 62;
  float starInnerScale = 24;
  uint starInnerScaleTag = 76;
  ArcData arcData = 195;
  VectorData vectorData = 48;
  uint vectorDataTag = 100;
  uint vectorOperationVersion = 425;
  TextPathStart textPathStart = 432;
  bool exportBackgroundDisabled = 119;
  Guide[] guides = 138;
  bool internalOnly = 142;
  ScrollDirection scrollDirection = 159;
  float cornerSmoothing = 160;
  Vector scrollOffset = 166;
  bool exportTextAsSVGText = 175;
  ScrollContractedState scrollContractedState = 178;
  Vector contractedSize = 179;
  string fixedChildrenDivider = 180;
  ScrollBehavior scrollBehavior = 186;
  int derivedSymbolDataLayoutVersion = 196;
  NavigationType navigationType = 197;
  OverlayPositionType overlayPositionType = 198;
  Vector overlayRelativePosition = 199;
  OverlayBackgroundInteraction overlayBackgroundInteraction = 200;
  OverlayBackgroundAppearance overlayBackgroundAppearance = 201;
  GUID overrideKey = 213;
  bool containerSupportsFillStrokeAndCorners = 220;
  StackSize stackCounterSizing = 221;
  bool containersSupportFillStrokeAndCorners = 222;
  KeyTrigger keyTrigger = 224;
  string voiceEventPhrase = 227;
  GUID[] ancestorPathBeforeDeletion = 235;
  SymbolLink[] symbolLinks = 237;
  TextListData textListData = 239;
  bool detachOpticalSizeFromFontSize = 261;
  float listSpacing = 264;
  EmbedData embedData = 270;
  RichMediaData richMediaData = 272;
  MultiplayerMap renderedSyncedState = 277;
  bool simplifyInstancePanels = 284;
  HTMLTag accessibleHTMLTag = 302;
  ARIARole ariaRole = 303;
  ARIAAttributesMap ariaAttributes = 357;
  string accessibleLabel = 304;
  bool isDecorativeImage = 490;
  VariableData variableData = 306;
  VariableDataMap variableConsumptionMap = 307;
  VariableModeBySetMap variableModeBySetMap = 316;
  VariableSetMode[] variableSetModes = 312;
  VariableSetID variableSetID = 313;
  VariableResolvedDataType variableResolvedType = 314;
  VariableDataValues variableDataValues = 315;
  string variableTokenName = 350;
  VariableTimingDisplayUnit timingDisplayUnit = 566;
  VariableScope[] variableScopes = 353;
  VariableDataMap parameterConsumptionMap = 445;
  CodeSyntaxMap codeSyntax = 358;
  PasteSource pasteSource = 388;
  EditorType pageType = 397;
  GUID strokeBrushGuid = 446;
  uint64 strokeSeed = 482;
  VariableWidthPoint[] variableWidthPoints = 447;
  DynamicStrokeSettings dynamicStrokeSettings = 448;
  ScatterStrokeSettings scatterStrokeSettings = 449;
  StretchStrokeSettings stretchStrokeSettings = 450;
  Matrix[] scatterBrushTransforms = 488;
  BrushType brushType = 452;
  OptionalVector pathTrim = 542;
  float strokeOffset = 554;
  VariableSetID backingVariableSetId = 377;
  VariableID overriddenVariableId = 464;
  VariableIdOrVariableOverrideId backingVariableId = 378;
  bool isCollectionExtendable = 385;
  string rootVariableKey = 386;
  InheritedVariablesData inheritedVariableIds = 517;
  HandoffStatusMap handoffStatusMap = 361;
  AgendaPositionMap agendaPositionMap = 327;
  AgendaMetadataMap agendaMetadataMap = 328;
  MigrationStatus migrationStatus = 329;
  bool isSoftDeleted = 330;
  EditInfo editInfo = 331;
  ColorProfile colorProfile = 341;
  SymbolId detachedSymbolId = 342;
  ChildReadingDirection childReadingDirection = 346;
  string readingIndex = 347;
  DocumentColorProfile documentColorProfile = 349;
  DeveloperRelatedLink[] developerRelatedLinks = 354;
  string slideActiveThemeLibKey = 356;
  EditScopeInfo editScopeInfo = 365;
  SemanticWeight semanticWeight = 374;
  SemanticItalic semanticItalic = 375;
  bool areSlidesManuallyIndented = 403;
  bool isResponsiveSet = 387;
  DerivedBreakpointData derivedBreakpointData = 500;
  GUID defaultResponsiveSetId = 398;
  bool isPrimaryBreakpoint = 458;
  GUID primaryResponsiveNodeId = 457;
  GUID multiEditGlueId = 462;
  float breakpointMinWidth = 501;
  bool isBreakpointInFocus = 522;
  ResponsiveSetSettings responsiveSetSettings = 400;
  NodeBehaviors behaviors = 404;
  string sourceCode = 414;
  CollaborativeTextOpID[] sourceCodeCollaborativeTextVersion = 534;
  CollaborativePlainText collaborativeSourceCode = 444;
  CodeLibraryId belongsToCodeLibraryId = 427;
  ImportedCodeFiles importedCodeFiles = 467;
  CanvasNodeId codeFileCanvasNodeId = 468;
  bool isEntrypointCodeFile = 498;
  string componentOrStateGroupKey = 502;
  uint componentOrStateGroupVersion = 503;
  string sourceCodeLibraryKey = 504;
  string[] sourceCodeLibraryKeys = 515;
  UsedMakeLibrary[] usedMakeLibraries = 524;
  string makeLibraryComponentId = 518;
  bool shouldHidePreviewForMakeKitCreation = 520;
  bool isMakeKit = 551;
  PrototypeDevice codePreviewSettings = 531;
  CodeExample[] codeExamples = 525;
  CodeFileId exportedFromCodeFileId = 428;
  string codeExportName = 430;
  string codeComponentDescription = 547;
  CodeComponentId backingCodeComponentId = 429;
  bool isMainCodeComponent = 487;
  CodeSnapshotState codeSnapshotState = 431;
  NodeChatMessage[] chatMessages = 451;
  NodeChatCompressionState chatCompressionState = 485;
  AIChatThread aiChatThread = 496;
  string codeChatMessagesKey = 484;
  CodeSnapshot codeSnapshot = 459;
  CodeSnapshotLayers codeSnapshotLayers = 589;
  uint codeSnapshotInvalidatedAt = 480;
  bool isCodeBehavior = 465;
  bool autoForkCode = 469;
  bool hasBeenManuallyRenamed = 470;
  bool codeCreatedFromDesign = 471;
  CanvasNodeId codeCreatedFromDesignNodeId = 481;
  ImageImportMap imageImports = 473;
  CodeObjectType codeObjectType = 516;
  string codeFilePath = 472;
  CodeBehaviorData codeBehaviorData = 478;
  uint codeLibraryFormat = 519;
  bool isCodePreviewPlayingOnCanvas = 527;
  CodeEmbedInfo codeEmbedInfo = 532;
  bool isEmbedCodeLayer = 546;
  CodeSourceInfo codeSourceInfo = 558;
  string mimeType = 536;
  byte[] blobRef = 537;
  CMSSelector cmsSelector = 419;
  CMSConsumptionMap cmsConsumptionMap = 420;
  CMSRichTextStyleMap cmsRichTextStyleMap = 460;
  SymbolId repeaterSymbolId = 539;
  RepeaterCmsOverrideData repeaterCmsOverrideData = 540;
  RepeaterSymbolOverrideData repeaterSymbolOverrideData = 549;
  RepeaterOverrideData repeaterOverrideData = 541;
  uint[] aiEditedNodeChangeFieldNumbers = 405;
  string aiEditScopeLabel = 408;
  FirstDraftData firstDraftData = 407;
  FirstDraftKitElementData firstDraftKitElementData = 418;
  CooperRevertData cooperRevertData = 421;
  CooperTemplateData cooperTemplateData = 461;
  BuzzApprovalRequests buzzApprovalRequests = 528;
  BuzzApprovalNodeStatusInfo buzzApprovalNodeStatusInfo = 529;
  HubFileAttribution hubFileAttribution = 422;
  ManagedStringData managedStringData = 454;
  ThumbnailInfo thumbnailInfo = 466;
  AiCanvasPrompt aiCanvasPrompt = 494;
  CanvasNodeId backingNodeId = 499;
  string pageStatus = 548;
  TRSSTransform2D motionTransform = 523;
  int64 timelinePosition = 506;
  KeyframeValueData keyframeValue = 507;
  VariableData keyframeValueRef = 586;
  InterpolationType interpolationType = 505;
  BezierHandles bezierHandles = 508;
  EasingData easingData = 535;
  KeyframeOperation keyframeOperation = 509;
  TimelinePositionType timelinePositionType = 510;
  bool isClip = 545;
  GUID clipId = 511;
  uint64 timelineDuration = 512;
  int64 timelineOffset = 513;
  bool timelineDisabled = 543;
  PlaybackStyle playbackStyle = 514;
  TimelineDefinitionsMap timelineDefinitions = 552;
  TimelineAssignmentsMap timelineAssignments = 553;
  AnimationPresets animationPresets = 521;
  StyleIdForAnimation[] styleIdsForAnimation = 582;
  AnimationPresetId backingAnimationPresetId = 583;
  Tools tools = 568;
  CustomEffects customEffects = 569;
  TransitionOverrideData transitionOverrides = 526;
  bool useLegacySmartAnimate = 544;
  SourceControlConfig sourceControlConfig = 557;
  SpecBlockType specBlockType = 559;
  CollaborativePlainText specBlockContent = 560;
  string specCodeBlockLanguage = 561;
  string specBlockTableAlignment = 562;
  int specBlockIndentLevel = 563;
  string specImageHash = 575;
  int specWidth = 576;
  int specHeight = 577;
  TableRowColumnSizeMap specBlockTableRowHeights = 578;
  TableRowColumnSizeMap specBlockTableColumnWidths = 579;
  string specEmbedUrl = 590;
  bool placeholder = 565;
  string placeholderClientLifecycleId = 584;
  int placeholderInvalidateAt = 585;
  bool disableJitDst = 567;
  CustomToolArtifactRef customToolArtifactRef = 588;
}

enum GitRepoRefProvider {
  UGIT = 0;
  GITHUB = 1;
  OTHER = 2;
}

message GitRepoRef {
  GitRepoRefProvider provider = 1;
  string gitRepo = 2;
  string gitRef = 3;
}

message SourceControlConfig {
  GitRepoRef origin = 1;
  GitRepoRef upstream = 2;
}

message CodeSnapshot {
  CodeSnapshotState state = 6;
  uint invalidatedAt = 7;
  Paint[] paints = 1;
  Vector offset = 2;
  Vector layoutSize = 3;
  Vector canvasSize = 5;
  uint devicePixelRatio = 4;
}

message CodeOutputLayer {
  NodeChange node = 1;
}

message CodeSnapshotLayers {
  CodeSnapshotState state = 1;
  uint invalidatedAt = 2;
  CodeOutputLayer[] layers = 3;
}

message CodeBehaviorData {
  string name = 1;
  string icon = 2;
  string[] nodeTypes = 3;
  string category = 4;
  uint apiVersion = 5;
}

message CodeExample {
  string exampleName = 1;
  string codeExportName = 2;
}

message UsedMakeLibrary {
  string makeLibraryId = 1;
}

message CookieBannerText {
  string bannerHeader = 1;
  string bannerDisclaimerExplicit = 2;
  string bannerDisclaimerImplicit = 3;
  string policyLabel = 4;
  string acceptText = 5;
  string acknowledgeText = 6;
  string manageText = 7;
  string rejectText = 8;
  string necessaryText = 9;
  string necessaryDescription = 10;
  string analyticsText = 11;
  string analyticsDescription = 12;
  string preferencesText = 13;
  string preferencesDescription = 14;
  string marketingText = 15;
  string marketingDescription = 16;
  string saveLabel = 17;
  string triggerLabel = 18;
}

message CookieBannerSettings {
  bool enabled = 1;
  CookieBannerComponentType componentType = 2;
  CookieXAlignment xAlignment = 3;
  CookieYAlignment yAlignment = 4;
  CookieXAlignment triggerXAlignment = 5;
  CookieYAlignment triggerYAlignment = 6;
  TriggerComponentType triggerComponentType = 7;
  string policyUrl = 8;
  CookieBannerText text = 9;
  GUID policyLink = 10;
  string locale = 11;
}

enum CookieBannerComponentType {
  BANNER = 0;
  MODAL = 1;
}

enum TriggerComponentType {
  BANNER = 0;
  TAG = 1;
}

enum CookieXAlignment {
  LEFT = 0;
  CENTER = 1;
  RIGHT = 2;
}

enum CookieYAlignment {
  TOP = 0;
  CENTER = 1;
  BOTTOM = 2;
}

message ResponsiveSetSettings {
  string title = 1;
  string description = 2;
  ResponsiveScalingMode scalingMode = 3;
  float scalingMinFontSize = 4;
  float scalingMaxFontSize = 5;
  float scalingMinLayoutWidth = 6;
  float scalingMaxLayoutWidth = 7;
  string lang = 8;
  string faviconHash = 9;
  string socialImageHash = 10;
  string googleAnalyticsID = 11;
  bool blockSearchIndexing = 12;
  string customCodeHeadStart = 13;
  string customCodeHeadEnd = 14;
  string customCodeBodyStart = 15;
  string customCodeBodyEnd = 16;
  GUID faviconID = 17;
  GUID socialImageID = 18;
  bool addBypassLinks = 19;
  bool ignoreReducedMotion = 20;
  CookieBannerSettings cookieBanner = 21;
}

enum ResponsiveScalingMode {
  REFLOW = 0;
  SCALE = 1;
}

message CMSSelector {
  string cmsCollectionId = 1;
  CMSFilterCritera filterCriteria = 2;
  CMSSelectorSort[] sorts = 3;
  uint limit = 4;
}

message CMSFilterCritera {
  CMSFilterCriteriaMatchType matchType = 1;
  CMSSelectorFilter[] filters = 2;
}

enum CMSFilterCriteriaMatchType {
  MATCH_ALL = 0;
  MATCH_ANY = 1;
}

message CMSSelectorFilter {
  string cmsFieldId = 1;
  CMSSelectorFilterOperator op = 2;
  string comparisonValue = 3;
}

enum CMSSelectorFilterOperator {
  EQUALS = 0;
}

message CMSSelectorSort {
  string cmsFieldId = 1;
  CMSFieldOrderBy orderBy = 2;
}

enum CMSFieldOrderBy {
  ASCENDING = 0;
  DESCENDING = 1;
}

message CMSConsumptionMap {
  CMSConsumptionMapEntry[] entries = 1;
}

message CMSConsumptionMapEntry {
  CMSConsumptionField consumptionField = 1;
  string cmsFieldId = 2;
}

enum CMSConsumptionField {
  MISSING = 0;
  TEXT_DATA = 1;
}

message CMSRichTextStyleMap {
  CMSRichTextStyleEntry[] entries = 1;
}

message CMSRichTextStyleEntry {
  CMSRichTextStyleClass styleClass = 1;
  CMSRichTextDescriptor textDescriptor = 2;
}

enum CMSRichTextStyleClass {
  HEADING1 = 0;
  HEADING2 = 1;
  HEADING3 = 2;
  HEADING4 = 3;
  HEADING5 = 4;
  HEADING6 = 5;
  PARAGRAPH = 6;
  LINK = 7;
  BLOCKQUOTE = 8;
}

message CMSRichTextDescriptor {
  StyleId textStyleId = 1;
  FontName[] fontNameVariants = 2;
}

message RepeaterCmsOverrideData {
  NodeChange[] overrides = 1;
}

message RepeaterOverrideData {
  NodeChange[] parentIndexOverrides = 1;
}

message RepeaterSymbolOverrideData {
  RepeaterPositionOverrides[] overridesByPosition = 1;
}

message RepeaterPositionOverrides {
  ParentIndex position = 1;
  NodeChange[] overrides = 2;
}

message InheritedVariablesData {
  InheritedVariableEntry[] variableIds = 1;
}

message InheritedVariableEntry {
  VariableID variableId = 1;
}

message HubFileAttribution {
  string hubFileId = 1;
  string hubFileName = 2;
}

message ManagedStringData {
  string key = 1;
  string context = 2;
  string locale = 3;
  ManagedStringNode content = 4;
  ManagedStringContentSchema contentSchema = 5;
}

enum ManagedStringContentSchema {
  V0 = 0;
}

enum ManagedStringNodeType {
  TEXT = 0;
  CONCATENATE = 1;
  PLURAL = 2;
  PLACEHOLDER = 3;
}

message ManagedStringNode {
  ManagedStringNodeType type = 1;
  ManagedStringTextNodeData textNodeData = 2;
  ManagedStringConcatenateAstNodeData concatenateNodeData = 3;
  ManagedStringPluralAstNodeData pluralNodeData = 4;
  ManagedStringPlaceholderAstNodeData placeholderNodeData = 5;
}

message ManagedStringTextNodeData {
  string value = 1;
}

message ManagedStringConcatenateAstNodeData {
  ManagedStringNode[] values = 1;
}

enum ManagedStringPluralType {
  ZERO = 0;
  ONE = 1;
  TWO = 2;
  FEW = 3;
  MANY = 4;
  OTHER = 5;
}

message ManagedStringPluralAstNodeData {
  string identifier = 1;
  ManagedStringPluralTypeMapEntry[] conditions = 2;
}

message ManagedStringPluralTypeMapEntry {
  ManagedStringPluralType key = 1;
  ManagedStringNode value = 2;
}

enum ManagedStringFormatType {
  TEXT = 0;
  DATE = 1;
  TIME = 2;
  NUMBER = 3;
}

message ManagedStringPlaceholderAstNodeData {
  string identifier = 1;
  ManagedStringFormatType formatType = 2;
  string formatPattern = 3;
}

message CooperRevertData {
  NodeChange originalValues = 1;
}

message VideoPlayback {
  bool autoplay = 1;
  bool mediaLoop = 2;
  bool muted = 3;
  bool showControls = 4;
  uint startTimeMs = 5;
  uint endTimeMs = 6;
}

enum MediaAction {
  PLAY = 0;
  PAUSE = 1;
  TOGGLE_PLAY_PAUSE = 2;
  MUTE = 3;
  UNMUTE = 4;
  TOGGLE_MUTE_UNMUTE = 5;
  SKIP_FORWARD = 6;
  SKIP_BACKWARD = 7;
  SKIP_TO = 8;
  SET_PLAYBACK_RATE = 9;
}

enum AnimationTimelineAction {
  PLAY = 0;
  PAUSE = 1;
  TOGGLE_PLAY_PAUSE = 2;
  SET_PLAYHEAD = 3;
}

message WidgetHoverStyle {
  Paint[] fillPaints = 1;
  Paint[] strokePaints = 2;
  float opacity = 3;
  bool areFillPaintsSet = 4;
  bool areStrokePaintsSet = 5;
  bool isOpacitySet = 6;
}

message WidgetDerivedSubtreeCursor {
  uint sessionID = 1;
  uint counter = 2;
}

message MultiplayerMap {
  MultiplayerMapEntry[] entries = 1;
}

message MultiplayerMapEntry {
  string key = 1;
  string value = 2;
}

message VariableDataMap {
  VariableDataMapEntry[] entries = 1;
}

message VariableDataMapEntry {
  uint nodeField = 1;
  VariableData variableData = 2;
  VariableField variableField = 3;
}

enum VariableField {
  MISSING = 0;
  CORNER_RADIUS = 1;
  PARAGRAPH_SPACING = 2;
  PARAGRAPH_INDENT = 3;
  STROKE_WEIGHT = 4;
  STACK_SPACING = 5;
  STACK_PADDING_LEFT = 6;
  STACK_PADDING_TOP = 7;
  STACK_PADDING_RIGHT = 8;
  STACK_PADDING_BOTTOM = 9;
  VISIBLE = 10;
  TEXT_DATA = 11;
  WIDTH = 12;
  HEIGHT = 13;
  RECTANGLE_TOP_LEFT_CORNER_RADIUS = 14;
  RECTANGLE_TOP_RIGHT_CORNER_RADIUS = 15;
  RECTANGLE_BOTTOM_LEFT_CORNER_RADIUS = 16;
  RECTANGLE_BOTTOM_RIGHT_CORNER_RADIUS = 17;
  BORDER_TOP_WEIGHT = 18;
  BORDER_BOTTOM_WEIGHT = 19;
  BORDER_LEFT_WEIGHT = 20;
  BORDER_RIGHT_WEIGHT = 21;
  VARIANT_PROPERTIES = 22;
  STACK_COUNTER_SPACING = 23;
  MIN_WIDTH = 24;
  MAX_WIDTH = 25;
  MIN_HEIGHT = 26;
  MAX_HEIGHT = 27;
  FONT_FAMILY = 28;
  FONT_STYLE = 29;
  FONT_VARIATIONS = 30;
  OPACITY = 31;
  FONT_SIZE = 32;
  LETTER_SPACING = 34;
  LINE_HEIGHT = 36;
  OVERRIDDEN_SYMBOL_ID = 37;
  HYPERLINK = 38;
  CMS_SERIALIZED_RICH_TEXT_DATA = 39;
  SLOT_CONTENT_ID = 40;
  GRID_ROW_GAP = 41;
  GRID_COLUMN_GAP = 42;
  X_POSITION = 43;
  Y_POSITION = 44;
  ROTATION = 45;
  MOTION_TRANSLATION_X = 46;
  MOTION_TRANSLATION_Y = 47;
  MOTION_ROTATION = 48;
  MOTION_SCALE_X = 49;
  MOTION_SCALE_Y = 50;
  MOTION_SHEAR = 51;
  SCROLL_OFFSET_X = 52;
  SCROLL_OFFSET_Y = 53;
  PATH_TRIM_START = 54;
  PATH_TRIM_END = 55;
  DISSOLVE_PROGRESS = 56;
  EASING_DATA = 57;
  MEDIA_CURRENT_TIME = 58;
  TRANSFORM_3D_PERSPECTIVE = 59;
  TRANSFORM_3D_TRANSLATION_Z = 60;
  TRANSFORM_3D_ROTATION_X = 61;
  TRANSFORM_3D_ROTATION_Y = 62;
  TRANSFORM_3D_ROTATION_Z = 63;
  POLYGON_COUNT = 64;
  ARC_DATA_STARTING_ANGLE = 65;
  ARC_DATA_ENDING_ANGLE = 66;
  ARC_DATA_INNER_RADIUS = 67;
}

message VariableModeBySetMap {
  VariableModeBySetMapEntry[] entries = 1;
}

message VariableModeBySetMapEntry {
  VariableSetID variableSetID = 1;
  GUID variableModeID = 2;
  VariableSetID variableSetExtensionID = 3;
}

message CodeSyntaxMap {
  CodeSyntaxMapEntry[] entries = 1;
}

message CodeSyntaxMapEntry {
  CodeSyntaxPlatform platform = 1;
  string value = 2;
}

message TableMergedCellMap {
  TableMergedCellMapEntry[] entries = 1;
}

message TableMergedCellMapEntry {
  GUID rowId = 1;
  GUID colId = 2;
  int rowSpan = 3;
  int colSpan = 4;
}

message TableRowColumnPositionMap {
  TableRowColumnPositionMapEntry[] entries = 1;
}

message TableRowColumnPositionMapEntry {
  GUID id = 1;
  string position = 2;
}

message GUIDPositionMap {
  GUIDPositionMapEntry[] entries = 1;
}

message GUIDPositionMapEntry {
  GUID id = 1;
  string position = 2;
}

message GUIDGridTrackSizeMap {
  GUIDGridTrackSizeMapEntry[] entries = 1;
}

message GUIDGridTrackSizeMapEntry {
  GUID id = 1;
  GridTrackSize trackSize = 2;
}

message ObjectAnimationList {
  ObjectAnimationListItem[] entries = 1;
}

message ObjectAnimationListItem {
  GUID targetNodeId = 1;
  PrototypeAction animation = 2;
}

message GridTrackSize {
  GridTrackSizingFunction minSizing = 1;
  GridTrackSizingFunction maxSizing = 2;
}

message GridTrackSizingFunction {
  GridTrackSizingType type = 1;
  float value = 2;
}

enum GridTrackSizingType {
  FLEX = 0;
  FIXED = 1;
  HUG = 2;
}

message TableRowColumnSizeMap {
  TableRowColumnSizeMapEntry[] entries = 1;
}

message TableRowColumnSizeMapEntry {
  GUID id = 1;
  float size = 2;
}

message AgendaPositionMap {
  AgendaPositionMapEntry[] entries = 1;
}

message AgendaPositionMapEntry {
  GUID id = 1;
  string position = 2;
}

enum AgendaItemType {
  NODE = 0;
  BLOCK = 1;
}

message AgendaMetadataMap {
  AgendaMetadataMapEntry[] entries = 1;
}

message AgendaMetadataMapEntry {
  GUID id = 1;
  AgendaMetadata data = 2;
}

message AgendaMetadata {
  string name = 1;
  AgendaItemType type = 2;
  GUID targetNodeID = 3;
  AgendaTimerInfo timerInfo = 4;
  AgendaVoteInfo voteInfo = 5;
  AgendaMusicInfo musicInfo = 6;
}

message AgendaTimerInfo {
  uint timerLength = 1;
}

message AgendaVoteInfo {
  uint voteCount = 1;
}

message AgendaMusicInfo {
  string songID = 1;
  uint startTimeMs = 2;
}

enum DiagramLayoutRuleType {
  NONE = 0;
  TREE = 1;
}

struct DiagramParentIndex {
  GUID guid;
  string position;
}

enum DiagramLayoutPaused {
  NO = 0;
  YES = 1;
}

message ComponentPropRef {
  uint nodeField = 1;
  GUID defID = 2;
  string zombieFallbackName = 3;
  ComponentPropNodeField componentPropNodeField = 4;
  bool isDeleted = 5;
}

enum ComponentPropNodeField {
  VISIBLE = 0;
  TEXT_DATA = 1;
  OVERRIDDEN_SYMBOL_ID = 2;
  INHERIT_FILL_STYLE_ID = 3;
  SLOT_CONTENT_ID = 4;
}

message ComponentPropAssignment {
  GUID defID = 1;
  ComponentPropValue value = 2;
  VariableData varValue = 3;
  DerivedTextData legacyDerivedTextData = 4;
}

message ComponentPropDef {
  GUID id = 1;
  string name = 2;
  ComponentPropValue initialValue = 3;
  string sortPosition = 4;
  GUID parentPropDefId = 5;
  ComponentPropType type = 6;
  bool isDeleted = 7;
  ComponentPropPreferredValues preferredValues = 8;
  VariableData varValue = 9;
  ParameterConfig parameterConfig = 10;
  string description = 11;
  SlotPropConfig slotPropConfig = 12;
  ColorArrayConfig colorArrayConfig = 13;
}

message ComponentPropValue {
  bool boolValue = 1;
  TextData textValue = 2;
  GUID guidValue = 3;
  float floatValue = 4;
  EasingData easingData = 5;
  Vector vectorValue = 6;
  Line lineValue = 7;
  Circle circleValue = 8;
  Rotation3D rotation3DValue = 9;
  CirclePoint circlePointValue = 10;
  Gradient gradientValue = 11;
  ColorPoint colorPointValue = 12;
}

message TimelineData {
  uint64 durationUs = 1;
  bool defaultTimeline = 2;
  GUID parentTimelineDefId = 3;
  PlaybackStyle playbackStyle = 4;
}

message TimelineDefinitionsMap {
  TimelineDefinitionsMapEntry[] entries = 1;
}

message TimelineDefinitionsMapEntry {
  GUID id = 1;
  TimelineData data = 2;
}

message TimelineAssignmentKey {
  GUID assignedTimelineId = 1;
  GUID containingTimelineId = 2;
}

message TimelineBindingData {
  int64 offsetUs = 1;
  bool disabled = 2;
}

message TimelineAssignmentsMap {
  TimelineAssignmentsMapEntry[] entries = 1;
}

message TimelineAssignmentsMapEntry {
  TimelineAssignmentKey key = 1;
  TimelineBindingData value = 2;
}

enum ComponentPropType {
  BOOL = 0;
  TEXT = 1;
  COLOR = 2;
  INSTANCE_SWAP = 3;
  VARIANT = 4;
  NUMBER = 5;
  IMAGE = 6;
  SLOT = 7;
  EASING = 8;
  COLOR_ARRAY = 9;
  VECTOR = 10;
  LINE = 11;
  CIRCLE = 12;
  ROTATION_3D = 13;
  CIRCLE_POINT = 14;
  GRADIENT = 15;
  COLOR_POINT = 16;
}

message ComponentPropPreferredValues {
  string[] stringValues = 1;
  InstanceSwapPreferredValue[] instanceSwapValues = 2;
}

message ParameterConfig {
  NumberPropConfig numberPropConfig = 1;
  ParameterConfigControl control = 2;
  SliderConfig sliderConfig = 3;
  VariableData label = 4;
  InputConfig inputConfig = 5;
  SelectConfig selectConfig = 6;
  PointConfig pointConfig = 7;
  LineConfig lineConfig = 8;
  PointRadiusConfig pointRadiusConfig = 9;
  Rotation3DConfig rotation3DConfig = 10;
  CirclePointConfig circlePointConfig = 11;
  ColorPointConfig colorPointConfig = 12;
  bool showDividerAbove = 13;
}

enum ParameterConfigControl {
  DEFAULT = 0;
  SLIDER = 1;
  INPUT = 2;
  SELECT = 3;
}

message InputConfig {
  VariableData unit = 1;
  VariableData min = 2;
  VariableData max = 3;
}

message SliderConfig {
  VariableData min = 1;
  VariableData max = 2;
  VariableData step = 3;
  VariableData unit = 4;
}

enum PointMode {
  CANVAS_AND_UI = 0;
  CANVAS = 1;
  UI = 2;
}

message PointConfig {
  PointMode mode = 1;
  NumberUnits unit = 2;
}

message Line {
  Vector a = 1;
  Vector b = 2;
}

message LineConfig {
  PointMode mode = 1;
  NumberUnits unit = 2;
}

message Circle {
  Vector center = 1;
  float radius = 2;
}

message PointRadiusConfig {
  PointMode mode = 1;
  NumberUnits positionUnit = 2;
  NumberUnits radiusUnit = 3;
  float minRadius = 4;
  float maxRadius = 5;
}

message Rotation3D {
  float x = 1;
  float y = 2;
  float z = 3;
  float translateZ = 4;
}

message Rotation3DConfig {
  PointMode mode = 1;
}

message CirclePoint {
  Vector center = 1;
  float radius = 2;
  float angle = 3;
}

message CirclePointConfig {
  PointMode mode = 1;
  NumberUnits positionUnit = 2;
  NumberUnits radiusUnit = 3;
  float minRadius = 4;
  float maxRadius = 5;
}

message ColorPoint {
  Vector point = 1;
  VariableData color = 2;
}

message ColorPointConfig {
  PointMode mode = 1;
  NumberUnits unit = 2;
}

message Gradient {
  GradientStop[] stops = 1;
}

message GradientStop {
  float position = 1;
  VariableData color = 2;
}

message SelectOption {
  VariableData value = 1;
  string label = 2;
}

message SelectConfig {
  SelectOption[] options = 1;
}

message ColorArrayConfig {
  uint minLength = 1;
  uint maxLength = 2;
}

message SlotPropConfig {
  bool stretchChildOnInsert = 1;
  bool displayByDefault = 2;
  uint minChildren = 3;
  uint maxChildren = 4;
  bool allowPreferredValuesOnly = 5;
}

message NumberPropConfig {
  ParameterConfigControl control = 1;
  VariableData min = 2;
  VariableData max = 3;
  VariableData step = 4;
}

message InstanceSwapPreferredValue {
  InstanceSwapPreferredValueType type = 1;
  string key = 2;
}

enum InstanceSwapPreferredValueType {
  COMPONENT = 0;
  STATE_GROUP = 1;
}

enum WidgetEvent {
  MOUSE_DOWN = 0;
  CLICK = 1;
  TEXT_EDIT_END = 2;
  ATTACHED_STICKABLES_CHANGED = 3;
  STUCK_STATUS_CHANGED = 4;
}

enum WidgetInputBehavior {
  WRAP = 0;
  TRUNCATE = 1;
  MULTILINE = 2;
}

message WidgetMetadata {
  string pluginID = 1;
  string pluginVersionID = 2;
  string widgetName = 3;
  bool isResizable = 4;
  bool isRotatable = 5;
}

enum WidgetPropertyMenuItemType {
  ACTION = 0;
  SEPARATOR = 1;
  COLOR = 2;
  DROPDOWN = 3;
  COLOR_SELECTOR = 4;
  TOGGLE = 5;
  LINK = 6;
}

message WidgetPropertyMenuSelectorOption {
  string option = 1;
  string tooltip = 2;
}

enum WidgetInputTextNodeType {
  WIDGET_CONTROLLED = 0;
  RICH_TEXT = 1;
}

message WidgetPropertyMenuItem {
  string propertyName = 1;
  string tooltip = 2;
  WidgetPropertyMenuItemType itemType = 3;
  string icon = 4;
  WidgetPropertyMenuSelectorOption[] options = 5;
  string selectedOption = 6;
  bool isToggled = 7;
  string href = 8;
  bool allowCustomColor = 9;
}

enum CodeBlockLanguage {
  TYPESCRIPT = 0;
  CPP = 1;
  RUBY = 2;
  CSS = 3;
  JAVASCRIPT = 4;
  HTML = 5;
  JSON = 6;
  GRAPHQL = 7;
  PYTHON = 8;
  GO = 9;
  SQL = 10;
  SWIFT = 11;
  KOTLIN = 12;
  RUST = 13;
  BASH = 14;
  PLAINTEXT = 15;
  MARKDOWN = 16;
}

enum CodeBlockTheme {
  FIGJAM_DARK = 0;
  DRACULA = 1;
  DUOTONE_SEA = 2;
  DUOTONE_SPACE = 3;
  DUOTONE_EARTH = 4;
  DUOTONE_FOREST = 5;
  DUOTONE_LIGHT = 6;
}

enum SpecBlockType {
  DEFAULT = 0;
  PARAGRAPH = 1;
  HEADING_1 = 2;
  HEADING_2 = 3;
  HEADING_3 = 4;
  HEADING_4 = 5;
  HEADING_5 = 6;
  HEADING_6 = 7;
  CODE_BLOCK = 8;
  BLOCK_QUOTE = 9;
  HORIZONTAL_RULE = 10;
  ORDERED_LIST_ITEM = 11;
  UNORDERED_LIST_ITEM = 12;
  DOCUMENT = 13;
  TABLE = 14;
  TABLE_ROW = 15;
  TABLE_CELL = 16;
  TODO_LIST_ITEM_UNCHECKED = 17;
  TODO_LIST_ITEM_CHECKED = 18;
  IMAGE = 19;
  EMBED = 20;
}

enum InternalEnumForTest {
  OLD = 1;
}

message InternalDataForTest {
  int testFieldA = 1;
}

message StateGroupPropertyValueOrder {
  string property = 1;
  string[] values = 2;
}

enum BackfillError {
  NONE = 0;
  TRANSIENT_RETRYING = 1;
  PERMANENTLY_FAILED = 2;
  PASTE_FAILED = 3;
}

message PartialPasteAnnotation {
  bool isPartial = 1;
  uint64 annotatedAt = 2;
  BackfillError errorState = 3;
}

message VariantPropSpec {
  GUID propDefId = 1;
  string value = 2;
}

message TextListData {
  int listID = 1;
  BulletType bulletType = 2;
  int indentationLevel = 3;
  int lineNumber = 4;
}

enum BulletType {
  ORDERED = 0;
  UNORDERED = 1;
  INDENT = 2;
  NO_LIST = 3;
}

message TextLineData {
  LineType lineType = 1;
  int styleId = 10;
  int indentationLevel = 2;
  SourceDirectionality sourceDirectionality = 9;
  Directionality directionality = 3;
  DirectionalityIntent directionalityIntent = 4;
  int downgradeStyleId = 5;
  int consistencyStyleId = 6;
  int listStartOffset = 7;
  bool isFirstLineOfList = 8;
}

message DerivedTextLineData {
  Directionality directionality = 1;
}

enum LineType {
  PLAIN = 0;
  ORDERED_LIST = 1;
  UNORDERED_LIST = 2;
  BLOCKQUOTE = 3;
  HEADER = 4;
}

enum SourceDirectionality {
  AUTO = 0;
  LTR = 1;
  RTL = 2;
}

enum Directionality {
  LTR = 0;
  RTL = 1;
}

enum DirectionalityIntent {
  IMPLICIT = 0;
  EXPLICIT = 1;
}

message PrototypeInteraction {
  GUID id = 1;
  PrototypeEvent event = 2;
  PrototypeAction[] actions = 3;
  bool isDeleted = 4;
  int stateManagementVersion = 5;
}

message PrototypeEvent {
  InteractionType interactionType = 1;
  bool interactionMaintained = 2;
  float interactionDuration = 3;
  KeyTrigger keyTrigger = 4;
  string voiceEventPhrase = 5;
  float transitionTimeout = 6;
  float mediaHitTime = 7;
}

message PrototypeVariableTarget {
  VariableID id = 1;
  NodeFieldAlias nodeFieldAlias = 2;
}

message ConditionalActions {
  PrototypeAction[] actions = 1;
  VariableData condition = 2;
}

message PrototypeAction {
  GUID transitionNodeID = 1;
  TransitionType transitionType = 2;
  float transitionDuration = 3;
  EasingType easingType = 4;
  float transitionTimeout = 5;
  bool transitionShouldSmartAnimate = 6;
  ConnectionType connectionType = 7;
  Vector overlayRelativePosition = 9;
  NavigationType navigationType = 10;
  bool transitionPreserveScroll = 11;
  float[] easingFunction = 12;
  Vector extraScrollOffset = 13;
  bool transitionResetScrollPosition = 25;
  bool transitionResetInteractiveComponents = 26;
  bool transitionOverridesEnabled = 42;
  string connectionURL = 8;
  bool openUrlInNewTab = 18;
  VariableData linkParam = 34;
  CMSItemPageTarget cmsTarget = 35;
  GUID targetVariableID = 14;
  VariableAnyValue targetVariableValue = 15;
  PrototypeVariableTarget targetVariable = 19;
  VariableData targetVariableData = 20;
  MediaAction mediaAction = 16;
  bool transitionResetVideoPosition = 17;
  float mediaSkipToTime = 21;
  float mediaSkipByAmount = 22;
  float mediaPlaybackRate = 36;
  VariableData[] conditions = 23;
  ConditionalActions[] conditionalActions = 24;
  VariableSetID targetVariableSetID = 27;
  GUID targetVariableModeID = 28;
  string targetVariableSetKey = 29;
  VariableSetID variableSetTargetExtensionId = 38;
  AnimationType animationType = 30;
  GUID animationTargetId = 31;
  AnimationPhase animationPhase = 32;
  AnimationState animationState = 33;
  bool simpleLink = 37;
  AnimationTimelineAction animationTimelineAction = 39;
  GUID animationTimelineDefId = 41;
  float animationSkipToTime = 40;
}

enum AnimationPhase {
  IN = 0;
  OUT = 1;
}

enum AnimationType {
  NONE = 0;
  FADE = 1;
  SLIDE_FROM_LEFT = 2;
  SLIDE_FROM_RIGHT = 3;
  SLIDE_FROM_TOP = 4;
  SLIDE_FROM_BOTTOM = 5;
}

message AnimationState {
  float opacity = 1;
  Matrix transform = 2;
}

message PrototypeStartingPoint {
  string name = 1;
  string description = 2;
  string position = 3;
}

enum TriggerDevice {
  KEYBOARD = 0;
  UNKNOWN_CONTROLLER = 1;
  XBOX_ONE = 2;
  PS4 = 3;
  SWITCH_PRO = 4;
}

message KeyTrigger {
  int[] keyCodes = 1;
  TriggerDevice triggerDevice = 2;
}

message Hyperlink {
  string url = 1;
  GUID guid = 2;
  CMSItemPageTarget cmsTarget = 4;
  bool openInNewTab = 3;
}

message CMSItemPageTarget {
  GUID nodeId = 1;
  string cmsItemId = 2;
  string fieldSchemaId = 3;
}

enum MentionSource {
  DEFAULT = 0;
  COPY_DUPLICATE = 1;
  SILENT_INSERT = 2;
}

message Mention {
  GUID id = 1;
  string mentionedUserId = 2;
  string mentionedByUserId = 3;
  string fileKey = 4;
  MentionSource source = 5;
  uint64 mentionedUserIdInt = 6;
  uint64 mentionedByUserIdInt = 7;
  string mentionedUserGroupId = 8;
}

message EmbedData {
  string url = 1;
  string srcUrl = 2;
  string title = 3;
  string thumbnailUrl = 4;
  float width = 5;
  float height = 6;
  string embedType = 7;
  string thumbnailImageHash = 8;
  string faviconImageHash = 9;
  string provider = 10;
  string originalText = 11;
  string description = 12;
  string embedVersionId = 13;
  bool isPublishedSite = 14;
}

message StampData {
  string userId = 1;
  string votingSessionId = 2;
  string stampedByUserId = 3;
}

message LinkPreviewData {
  string url = 1;
  string title = 2;
  string provider = 3;
  string description = 4;
  string thumbnailImageHash = 5;
  string faviconImageHash = 6;
  float thumbnailImageWidth = 7;
  float thumbnailImageHeight = 8;
}

message Viewport {
  Rect canvasSpaceBounds = 1;
  bool pixelPreview = 2;
  float pixelDensity = 3;
  GUID canvasGuid = 4;
}

message Mouse {
  MouseCursor cursor = 1;
  Vector canvasSpaceLocation = 2;
  Rect canvasSpaceSelectionBox = 3;
  GUID canvasGuid = 4;
  uint cursorHiddenReason = 5;
}

struct Click {
  uint id;
  Vector point;
}

struct ScrollPosition {
  GUID node;
  Vector scrollOffset;
}

struct TriggeredOverlay {
  GUID overlayGuid;
  GUID hotspotGuid;
  GUID swapGuid;
}

message TriggeredOverlayData {
  GUID overlayGuid = 1;
  GUID hotspotGuid = 2;
  GUID swapGuid = 3;
  GUID prototypeInteractionGuid = 4;
  GUIDPath hotspotBlueprintId = 5;
}

message TriggeredSetVariableActionData {
  GUID nodeForFindingTopmostScreenId = 1;
  string targetVariableId = 2;
  string targetVariableData = 3;
  string resolvedVariableModes = 4;
}

message TriggeredSetVariableModeActionData {
  GUID nodeForFindingTopmostScreenId = 1;
  string targetVariableSetKey = 2;
  string targetVariableModeId = 3;
  VariableSetID targetVariableSetId = 4;
}

message VideoStateChangeData {
  GUID targetNodeId = 1;
  bool isPlaying = 2;
  bool isPlayingSound = 3;
  uint[] currentTimes = 4;
  uint actionTakenTimestamp = 5;
}

message EmbeddedPrototypeData {
  GUID nodeId = 1;
  uint sessionId = 2;
}

message PresentedState {
  GUID baseScreenID = 1;
  TriggeredOverlayData[] overlays = 2;
}

enum TransitionDirection {
  FORWARD = 0;
  REVERSE = 1;
}

message TopLevelPlaybackChange {
  PresentedState oldState = 1;
  PresentedState newState = 2;
  GUIDPath hotspotBlueprintID = 3;
  GUID interactionID = 4;
  bool isHotspotInNewPresentedState = 5;
  TransitionDirection direction = 6;
  GUIDPath instanceStablePath = 7;
}

message InstanceStateChange {
  GUID stateID = 1;
  GUID interactionID = 2;
  GUIDPath hotspotStablePath = 3;
  GUIDPath instanceStablePath = 4;
  PlaybackChangePhase phase = 5;
}

message TextCursor {
  Rect selectionBox = 1;
  GUID canvasGuid = 2;
  GUID textNodeGuid = 3;
}

message TextSelection {
  Rect[] selectionBoxes = 1;
  GUID canvasGuid = 2;
  GUID textNodeGuid = 3;
  Vector textSelectionRange = 4;
  GUID textNodeOrContainingIfGuid = 5;
  GUID tableCellRowId = 6;
  GUID tableCellColId = 7;
}

enum PlaybackChangePhase {
  INITIATED = 0;
  ABORTED = 1;
  COMMITTED = 2;
}

message PlaybackChangeKeyframe {
  PlaybackChangePhase phase = 1;
  float progress = 2;
  float timestamp = 3;
}

message StateMapping {
  GUIDPath stablePath = 1;
  TopLevelPlaybackChange lastTopLevelChange = 2;
  PlaybackChangeKeyframe lastTopLevelChangeStatus = 3;
  float timestamp = 4;
}

message ScrollMapping {
  GUIDPath blueprintID = 1;
  uint overlayIndex = 2;
  Vector scrollOffset = 3;
}

message PlaybackUpdate {
  TopLevelPlaybackChange lastTopLevelChange = 1;
  PlaybackChangeKeyframe lastTopLevelChangeStatus = 2;
  ScrollMapping[] scrollMappings = 3;
  float timestamp = 4;
  Vector pointerLocation = 5;
  bool isTopLevelFrameChange = 6;
  StateMapping[] stateMappings = 7;
}

message ChatMessage {
  string text = 1;
  string previousText = 2;
}

message VoiceMetadata {
  string connectedCallId = 1;
}

message AprilFunCursor {
  string id = 1;
  bool trailEnabled = 2;
}

message AprilFunFigPal {
  string customization = 1;
  string name = 2;
}

enum Heartbeat {
  FOREGROUND = 0;
  BACKGROUND = 1;
}

enum SitesViewState {
  FILE = 0;
  CODE = 1;
  DAKOTA = 2;
  SETTINGS = 3;
  INSERT = 4;
  VARIABLES = 5;
}

enum DesignFullPageViewState {
  NONE = 0;
  DESIGN_SYSTEM = 1;
  VARIABLES = 2;
}

message AgentInfo {
  string name = 1;
  string logo = 2;
  string oauthClientId = 3;
}

message UserChange {
  uint sessionID = 1;
  string stableSessionID = 44;
  bool connected = 2;
  string name = 3;
  Color color = 4;
  string imageURL = 5;
  Viewport viewport = 6;
  Mouse mouse = 7;
  GUID[] selection = 8;
  uint[] observing = 9;
  string deviceName = 10;
  Click[] recentClicks = 11;
  ScrollPosition[] scrollPositions = 12;
  TriggeredOverlay[] triggeredOverlays = 13;
  string userID = 14;
  GUID lastTriggeredHotspot = 15;
  GUID lastTriggeredPrototypeInteractionID = 16;
  uint lastTriggeredObjectAnimationIndex = 38;
  TriggeredOverlayData[] triggeredOverlaysData = 17;
  PlaybackUpdate[] playbackUpdates = 18;
  ChatMessage chatMessage = 19;
  VoiceMetadata voiceMetadata = 20;
  bool canWrite = 21;
  bool highFiveStatus = 22;
  InstanceStateChange[] instanceStateChanges = 23;
  TextCursor textCursor = 24;
  TextSelection textSelection = 25;
  uint connectedAtTimeS = 26;
  bool focusOnTextCursor = 27;
  Heartbeat heartbeat = 28;
  TriggeredSetVariableActionData[] triggeredSetVariableActionData = 29;
  VideoStateChangeData[] videoStateChangeData = 30;
  string clientID = 31;
  GUID focusedSlideId = 32;
  TriggeredSetVariableModeActionData[] triggeredSetVariableModeActionData = 33;
  AprilFunCursor aprilFunCursor = 34;
  EmbeddedPrototypeData[] embeddedPrototypeData = 35;
  GUID activeSlidesEmbeddablePrototype = 36;
  GUID[] activeEmbeddedPrototypes = 43;
  GUID activeCodeComponentId = 37;
  AprilFunFigPal aprilFunFigPal = 39;
  CollaborativeTextSelection collaborativeTextSelection = 40;
  SitesViewState sitesViewState = 41;
  NodeChatExchange[] nodeChatExchanges = 42;
  DesignFullPageViewState designFullPageViewState = 45;
  AgentInfo agentInfo = 46;
}

message InteractiveSlideElementChange {
  string userID = 1;
  string anonymousUserID = 2;
  GUID nodeID = 3;
  string responseData = 4;
}

message NodeStatusChange {
  GUID[] nodeIds = 1;
  SectionStatusInfo statusInfo = 2;
}

message BuzzApprovalAssetEntry {
  GUID assetNodeId = 1;
  bool approved = 2;
}

message BuzzApprovalChange {
  BuzzApprovalAssetEntry[] assetEntries = 1;
  GUID canvasGridNodeId = 2;
  string requestId = 3;
}

enum SceneGraphQueryBehavior {
  DEFAULT = 0;
  CONTAINING_PAGE = 1;
  PLUGIN = 2;
}

enum SceneGraphQueryMode {
  ADD = 0;
  SET = 1;
}

message SceneGraphQuery {
  GUID startingNode = 1;
  uint depth = 2;
  SceneGraphQueryBehavior behavior = 3;
}

message NodeChangesMetadata {
  uint blobsFieldOffset = 1;
}

message CursorReaction {
  string imageUrl = 1;
}

message TimerInfo {
  bool isPaused = 1;
  uint timeRemainingMs = 2;
  uint totalTimeMs = 3;
  uint timerID = 4;
  string setBy = 5;
  uint songID = 6;
  uint lastReceivedSongTimestampMs = 7;
  string songUUID = 8;
}

message MusicInfo {
  bool isPaused = 1;
  uint messageID = 2;
  string songID = 3;
  uint lastReceivedSongTimestampMs = 4;
  bool isStopped = 5;
}

message PresenterNomination {
  uint sessionID = 1;
  bool isCancelled = 2;
}

message PresenterInfo {
  uint sessionID = 1;
  PresenterNomination nomination = 2;
  bool isReconnected = 3;
}

message ClientBroadcast {
  uint sessionID = 1;
  CursorReaction cursorReaction = 2;
  TimerInfo timer = 3;
  PresenterInfo presenter = 4;
  PresenterInfo prototypePresenter = 5;
  MusicInfo music = 6;
}

enum PasteAssetType {
  UNKNOWN = 0;
  VARIABLE = 1;
}

message Message {
  MessageType type = 1;
  uint sessionID = 2;
  string stableSessionID = 42;
  uint ackID = 3;
  bool isRetransmission = 37;
  NodeChange[] nodeChanges = 4;
  UserChange[] userChanges = 5;
  InteractiveSlideElementChange interactiveSlideElementChange = 32;
  NodeStatusChange nodeStatusChange = 36;
  BuzzApprovalChange buzzApprovalChange = 44;
  Blob[] blobs = 6;
  uint blobBaseIndex = 30;
  string signalName = 7;
  Access access = 8;
  string styleSetName = 9;
  StyleSetType styleSetType = 10;
  StyleSetContentType styleSetContentType = 11;
  int pasteID = 12;
  Vector pasteOffset = 13;
  string pasteFileKey = 14;
  string signalPayload = 15;
  SceneGraphQuery[] sceneGraphQueries = 16;
  NodeChangesMetadata nodeChangesMetadata = 17;
  uint fileVersion = 18;
  bool pasteIsPartiallyOutsideEnclosingFrame = 19;
  GUID pastePageId = 20;
  bool isCut = 21;
  Message[] localUndoStack = 22;
  Message[] localRedoStack = 23;
  ClientBroadcast[] broadcasts = 24;
  uint reconnectSequenceNumber = 25;
  string pasteBranchSourceFileKey = 26;
  EditorType pasteEditorType = 27;
  string postSyncActions = 28;
  GUID[] publishedAssetGuids = 29;
  bool dirtyFromInitialLoad = 31;
  ClipboardSelectionRegion[] clipboardSelectionRegions = 33;
  EncodedOffsetsIndex encodedOffsetsIndex = 34;
  bool hasRepeatingContent = 35;
  uint64 sentTimestamp = 38;
  AnnotationCategory[] annotationCategories = 39;
  ClientRenderedMetadata clientRenderedMetadata = 40;
  PasteAssetType pasteAssetType = 41;
  ObjectAnimationList objectAnimations = 43;
  SceneGraphQueryMode sceneGraphQueryMode = 45;
}

message EncodedOffsetsIndex {
  uint nodeChangesFieldOffset = 1;
  uint nodeChangesFieldLength = 2;
  uint blobsFieldOffset = 3;
  GUIDAndEncodedOffset[] nodeChangeOffsets = 4;
}

struct GUIDAndEncodedOffset {
  GUID guid;
  uint offset;
}

message DiffChunk {
  uint[] nodeChanges = 1;
  NodePhase phase = 2;
  NodeChange displayNode = 3;
  GUID canvasId = 4;
  string canvasName = 5;
  bool canvasIsInternal = 6;
  uint[] chunksAffectingThisChunk = 7;
  NodeChange[] basisParentHierarchy = 8;
  NodeChange[] parentHierarchy = 9;
  GUID[] basisParentHierarchyGuids = 10;
  GUID[] parentHierarchyGuids = 11;
}

enum DiffType {
  BRANCHING = 0;
  NODE_CHANGES_ONLY = 1;
}

message DiffPayload {
  NodeChange[] nodeChanges = 1;
  Blob[] blobs = 2;
  DiffChunk[] diffChunks = 3;
  NodeChange[] diffBasis = 4;
  NodeChange[] basisParentNodeChanges = 5;
  NodeChange[] parentNodeChanges = 6;
  DiffType diffType = 7;
}

enum RichMediaType {
  ANIMATED_IMAGE = 0;
  VIDEO = 1;
}

message RichMediaData {
  string mediaHash = 1;
  RichMediaType richMediaType = 2;
}

enum VariableDataType {
  BOOLEAN = 0;
  FLOAT = 1;
  STRING = 2;
  ALIAS = 3;
  COLOR = 4;
  EXPRESSION = 5;
  MAP = 6;
  SYMBOL_ID = 7;
  FONT_STYLE = 8;
  TEXT_DATA = 9;
  INVALID = 10;
  NODE_FIELD_ALIAS = 11;
  CMS_ALIAS = 12;
  PROP_REF = 13;
  IMAGE = 14;
  MANAGED_STRING_ALIAS = 15;
  LINK = 16;
  JS_RUNTIME_ALIAS = 17;
  SLOT_CONTENT_ID = 18;
  DATE = 19;
  KEYFRAME_TRACK_ID = 20;
  KEYFRAME_TRACK_PARAMETER_DATA = 21;
  EASING = 22;
  TIMING = 23;
  VECTOR = 24;
  COLOR_ARRAY = 25;
  LINE = 26;
  CIRCLE = 27;
  ROTATION_3D = 28;
  CIRCLE_POINT = 29;
  GRADIENT = 30;
  COLOR_POINT = 31;
}

enum VariableResolvedDataType {
  BOOLEAN = 0;
  FLOAT = 1;
  STRING = 2;
  COLOR = 4;
  MAP = 5;
  SYMBOL_ID = 6;
  FONT_STYLE = 7;
  TEXT_DATA = 8;
  IMAGE = 9;
  LINK = 10;
  JS_RUNTIME_ALIAS = 11;
  SLOT_CONTENT_ID = 12;
  KEYFRAME_TRACK_ID = 13;
  KEYFRAME_TRACK_PARAMETER_DATA = 14;
  EASING = 15;
  TIMING = 16;
  VECTOR = 17;
  COLOR_ARRAY = 18;
  LINE = 19;
  CIRCLE = 20;
  ROTATION_3D = 21;
  CIRCLE_POINT = 22;
  GRADIENT = 23;
  COLOR_POINT = 24;
}

message VariableAnyValue {
  bool boolValue = 1;
  string textValue = 2;
  float floatValue = 3;
  VariableID alias = 4;
  Color colorValue = 5;
  Expression expressionValue = 6;
  VariableMap mapValue = 7;
  SymbolId symbolIdValue = 8;
  VariableFontStyle fontStyleValue = 9;
  TextData textDataValue = 10;
  NodeFieldAlias nodeFieldAliasValue = 11;
  CMSAlias cmsAliasValue = 12;
  PropRefValue propRefValue = 13;
  ImageParameterValue imageValue = 14;
  ManagedStringAlias managedStringAliasValue = 15;
  Hyperlink linkValue = 16;
  JsRuntimeAlias jsRuntimeAliasValue = 17;
  SlotContentId slotContentIdValue = 18;
  KeyframeTrackId keyframeTrackIdValue = 19;
  KeyframeTrackParameterValue keyframeTrackParameterValue = 20;
  EasingData easingValue = 21;
  Vector vectorValue = 22;
  ColorArray colorArrayValue = 23;
  Line lineValue = 24;
  Circle circleValue = 25;
  Rotation3D rotation3DValue = 26;
  CirclePoint circlePointValue = 27;
  Gradient gradientValue = 28;
  ColorPoint colorPointValue = 29;
}

enum ExpressionFunction {
  ADDITION = 0;
  SUBTRACTION = 1;
  RESOLVE_VARIANT = 2;
  MULTIPLY = 3;
  DIVIDE = 4;
  EQUALS = 5;
  NOT_EQUAL = 6;
  LESS_THAN = 7;
  LESS_THAN_OR_EQUAL = 8;
  GREATER_THAN = 9;
  GREATER_THAN_OR_EQUAL = 10;
  AND = 11;
  OR = 12;
  NOT = 13;
  STRINGIFY = 14;
  TERNARY = 15;
  VAR_MODE_LOOKUP = 16;
  NEGATE = 17;
  IS_TRUTHY = 18;
  KEYFRAME = 19;
}

message Expression {
  ExpressionFunction expressionFunction = 1;
  VariableData[] expressionArguments = 2;
}

message VariableMapValue {
  string key = 1;
  VariableData value = 2;
  GUID guidKey = 3;
}

message VariableMap {
  VariableMapValue[] values = 1;
}

message ColorArray {
  VariableData[] colors = 1;
}

message VariableFontStyle {
  VariableData asString = 1;
  VariableData asFloat = 2;
  VariableData asVariations = 3;
}

message ImageParameterValue {
  Image image = 1;
  Image imageThumbnail = 2;
  Image animatedImage = 6;
  string altText = 3;
  uint originalImageHeight = 4;
  uint originalImageWidth = 5;
  uint animationFrame = 7;
}

message ThumbnailInfo {
  GUID nodeID = 1;
  string thumbnailVersion = 2;
}

message AiCanvasPrompt {
  string userPrompt = 1;
  string authorId = 2;
  GUID[] parentNodeIds = 3;
}

message NodeFieldAlias {
  GUIDPath stablePathToNode = 1;
  NodeFieldAliasType nodeField = 2;
  string indexOrKey = 3;
}

enum NodeFieldAliasType {
  MISSING = 0;
  COMPONENT_PROP_ASSIGNMENTS = 1;
}

message CMSAlias {
  string collectionId = 1;
  string itemId = 2;
  string fieldId = 3;
  VariableDataType type = 4;
}

message JsRuntimeAlias {
  string lookupKey = 1;
}

message PropRefValue {
  GUID defId = 1;
}

message ManagedStringId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message ManagedStringPlaceholderMapEntry {
  string key = 1;
  string value = 2;
}

message SlotContentId {
  GUID guid = 1;
}

message ManagedStringAlias {
  ManagedStringId managedStringId = 1;
  ManagedStringPlaceholderMapEntry[] placeholderValues = 2;
}

message KeyframeTrackId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message AnimationPresetId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message ToolId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message CustomEffectId {
  GUID guid = 1;
  AssetRef assetRef = 2;
}

message TRSSTransform2D {
  Vector translation = 1;
  float rotation = 2;
  Vector scale = 3;
  float shearX = 4;
}

message VariableData {
  VariableAnyValue value = 1;
  VariableDataType dataType = 2;
  VariableResolvedDataType resolvedDataType = 3;
}

message VariableSetMode {
  GUID id = 1;
  string name = 2;
  string sortPosition = 3;
  VariableSetID parentVariableSetId = 4;
  GUID parentModeId = 5;
}

message VariableDataValues {
  VariableDataValuesEntry[] entries = 1;
}

message VariableDataValuesEntry {
  GUID modeID = 1;
  VariableData variableData = 2;
}

enum VariableScope {
  ALL_SCOPES = 0;
  TEXT_CONTENT = 1;
  CORNER_RADIUS = 2;
  WIDTH_HEIGHT = 3;
  GAP = 4;
  ALL_FILLS = 5;
  FRAME_FILL = 6;
  SHAPE_FILL = 7;
  TEXT_FILL = 8;
  STROKE = 9;
  STROKE_FLOAT = 10;
  EFFECT_FLOAT = 11;
  EFFECT_COLOR = 12;
  OPACITY = 13;
  FONT_STYLE = 14;
  FONT_FAMILY = 15;
  FONT_SIZE = 16;
  LINE_HEIGHT = 17;
  LETTER_SPACING = 18;
  PARAGRAPH_SPACING = 19;
  PARAGRAPH_INDENT = 20;
  FONT_VARIATIONS = 21;
  TRANSFORM = 22;
}

message KeyframeAnyValue {
  float floatValue = 1;
  Color colorValue = 2;
  TextData textDataValue = 3;
  Vector vectorValue = 4;
}

enum KeyframeValueType {
  FLOAT = 0;
  INVALID = 1;
  COLOR = 2;
  TEXT_DATA = 3;
  VECTOR = 4;
}

message KeyframeValueData {
  KeyframeAnyValue value = 1;
  KeyframeValueType valueType = 2;
}

enum KeyframeTrackParameterType {
  INVALID = 0;
  MANUAL = 1;
  ANIMATION_PRESET = 2;
}

message ManualKeyframeTrackParameter {
  KeyframeTrackId keyframeTrackId = 1;
  GUID timelineDefId = 2;
}

message AnimationPresetKeyframeTrackParameter {
  AnimationPresetId animationPresetId = 1;
  KeyframeTrackId keyframeTrackId = 2;
  GUID timelineDefId = 3;
}

message KeyframeTrackAnyParameter {
  ManualKeyframeTrackParameter manual = 1;
  AnimationPresetKeyframeTrackParameter animationPreset = 2;
  GUID animationStyleBindingId = 3;
}

message KeyframeTrackParameter {
  KeyframeTrackAnyParameter value = 1;
  KeyframeTrackParameterType type = 2;
}

message KeyframeTrackParameterValue {
  KeyframeTrackParameter[] parameters = 1;
}

message AnimationPresets {
  AnimationPresetData[] presets = 1;
}

message AnimationPresetData {
  AnimationPresetId animationPresetId = 1;
  GUID timelineDefId = 2;
}

message StyleAnimation {
  AnimationPresetId animationPresetId = 1;
}

message StyleIdForAnimation {
  GUID id = 1;
  GUID timelineDefId = 2;
  StyleId animationStyleId = 3;
  int64 timelineOffset = 4;
}

message Tools {
  ToolData[] tools = 1;
}

message ToolData {
  ToolId toolId = 1;
  CodeComponentId backingCodeComponentId = 2;
  ComponentPropAssignment[] componentPropAssignments = 3;
}

message CustomEffects {
  CustomEffectData[] customEffects = 1;
}

message CustomEffectData {
  CustomEffectId customEffectId = 1;
}

message SpringParams {
  float stiffness = 1;
  float damping = 2;
  float mass = 3;
}

message TransitionEasingAnyValue {
  SpringParams springEasing = 1;
  BezierHandles bezierEasing = 2;
}

message EasingData {
  EasingType easingType = 1;
  TransitionEasingAnyValue easingValue = 2;
}

message TransitionOverride {
  GUID id = 1;
  float duration = 2;
  VariableData durationVar = 3;
  float delay = 4;
  VariableData delayVar = 5;
  EasingData easing = 6;
  VariableData easingVar = 7;
  uint64 createdAtMs = 8;
  GUID[] interactionIDs = 9;
  bool disabled = 10;
}

message TransitionOverrideData {
  TransitionOverride[] all = 1;
  TransitionOverridePropMap propertyOverrides = 2;
}

enum TransitionOverrideProp {
  ALL = 0;
  OPACITY = 1;
  TRANSLATION = 2;
  ROTATION = 3;
  SCALE = 4;
}

enum TransitionOverrideBindingTopLevelField {
  MISSING = 0;
  PARAMETER_CONSUMPTION_MAP = 1;
  EFFECT_DATA = 2;
  FILL_PAINT_DATA = 3;
  STROKE_PAINT_DATA = 4;
}

message TransitionOverrideBindingLocation {
  TransitionOverrideBindingTopLevelField topLevelField = 1;
  int parameterFieldValue = 2;
  int index = 3;
  int effectParametrizedFieldValue = 4;
}

enum KeyframeBindingEffectParametrizedField {
  MISSING = 0;
  OFFSET_X = 1;
  OFFSET_Y = 2;
  RADIUS = 3;
  SPREAD = 4;
  COLOR = 5;
  REFRACTION_RADIUS = 6;
  SPECULAR_ANGLE = 7;
  SPECULAR_INTENSITY = 8;
  CHROMATIC_ABERRATION = 9;
  SPLAY = 10;
  REFRACTION_INTENSITY = 11;
  START_RADIUS = 12;
  START_OFFSET_X = 13;
  START_OFFSET_Y = 14;
  END_OFFSET_X = 15;
  END_OFFSET_Y = 16;
  NOISE_SIZE_X = 17;
  NOISE_SIZE_Y = 18;
  DENSITY = 19;
  EFFECT_OPACITY = 20;
  SECONDARY_COLOR = 21;
}

message NodeContentsKeyframeBindingLocation {
  TransitionOverrideBindingTopLevelField topLevelField = 1;
  VariableField parameterFieldValue = 2;
  int index = 3;
  KeyframeBindingEffectParametrizedField effectParametrizedFieldValue = 4;
}

message TransitionOverridePropMap {
  TransitionOverridePropMapEntry[] entries = 1;
}

message TransitionOverridePropMapEntry {
  TransitionOverrideProp prop = 1;
  TransitionOverride[] overrides = 2;
  TransitionOverrideBindingLocation bindingLocation = 3;
  NodeContentsKeyframeBindingLocation nodeContentsBindingLocation = 4;
}

enum CodeSyntaxPlatform {
  WEB = 0;
  ANDROID = 1;
  iOS = 2;
}

message OptionalVector {
  Vector value = 1;
}

enum HTMLTag {
  AUTO = 0;
  ARTICLE = 1;
  SECTION = 2;
  NAV = 3;
  ASIDE = 4;
  H1 = 5;
  H2 = 6;
  H3 = 7;
  H4 = 8;
  H5 = 9;
  H6 = 10;
  HGROUP = 11;
  HEADER = 12;
  FOOTER = 13;
  ADDRESS = 14;
  P = 15;
  HR = 16;
  PRE = 17;
  BLOCKQUOTE = 18;
  OL = 19;
  UL = 20;
  MENU = 21;
  LI = 22;
  DL = 23;
  DT = 24;
  DD = 25;
  FIGURE = 26;
  FIGCAPTION = 27;
  MAIN = 28;
  DIV = 29;
  A = 30;
  EM = 31;
  STRONG = 32;
  SMALL = 33;
  S = 34;
  CITE = 35;
  Q = 36;
  DFN = 37;
  ABBR = 38;
  RUBY = 39;
  RT = 40;
  RP = 41;
  DATA = 42;
  TIME = 43;
  CODE = 44;
  VAR = 45;
  SAMP = 46;
  KBD = 47;
  SUB = 48;
  SUP = 49;
  I = 50;
  B = 51;
  U = 52;
  MARK = 53;
  BDI = 54;
  BDO = 55;
  SPAN = 56;
  BR = 57;
  WBR = 58;
  PICTURE = 59;
  SOURCE = 60;
  IMG = 61;
  FORM = 62;
  LABEL = 63;
  INPUT = 64;
  BUTTON = 65;
  SELECT = 66;
  DATALIST = 67;
  OPTGROUP = 68;
  OPTION = 69;
  TEXTAREA = 70;
  OUTPUT = 71;
  PROGRESS = 72;
  METER = 73;
  FIELDSET = 74;
  LEGEND = 75;
  VIDEO = 76;
}

enum ARIARole {
  AUTO = 0;
  NONE = 52;
  APPLICATION = 30;
  BANNER = 67;
  COMPLEMENTARY = 68;
  CONTENTINFO = 69;
  FORM = 70;
  MAIN = 71;
  NAVIGATION = 72;
  REGION = 73;
  SEARCH = 74;
  SEPARATOR = 13;
  ARTICLE = 31;
  COLUMNHEADER = 35;
  DEFINITION = 36;
  DIRECTORY = 38;
  DOCUMENT = 39;
  GROUP = 44;
  HEADING = 45;
  IMG = 46;
  LIST = 48;
  LISTITEM = 49;
  MATH = 50;
  NOTE = 53;
  PRESENTATION = 55;
  ROW = 56;
  ROWGROUP = 57;
  ROWHEADER = 58;
  TABLE = 62;
  TOOLBAR = 65;
  BUTTON = 1;
  CHECKBOX = 2;
  GRIDCELL = 3;
  LINK = 4;
  MENUITEM = 5;
  MENUITEMCHECKBOX = 6;
  MENUITEMRADIO = 7;
  OPTION = 8;
  PROGRESSBAR = 9;
  RADIO = 10;
  SCROLLBAR = 11;
  SLIDER = 14;
  SPINBUTTON = 15;
  TAB = 17;
  TABPANEL = 18;
  TEXTBOX = 19;
  TREEITEM = 20;
  COMBOBOX = 21;
  GRID = 22;
  LISTBOX = 23;
  MENU = 24;
  MENUBAR = 25;
  RADIOGROUP = 26;
  TABLIST = 27;
  TREE = 28;
  TREEGRID = 29;
  TOOLTIP = 66;
  ALERT = 75;
  LOG = 76;
  MARQUEE = 77;
  STATUS = 78;
  TIMER = 79;
  ALERTDIALOG = 80;
  DIALOG = 81;
  SEARCHBOX = 12;
  SWITCH = 16;
  BLOCKQUOTE = 32;
  CAPTION = 33;
  CELL = 34;
  DELETION = 37;
  EMPHASIS = 40;
  FEED = 41;
  FIGURE = 42;
  GENERIC = 43;
  INSERTION = 47;
  METER = 51;
  PARAGRAPH = 54;
  STRONG = 59;
  SUBSCRIPT = 60;
  SUPERSCRIPT = 61;
  TERM = 63;
  TIME = 64;
  IMAGE = 82;
  HEADING_1 = 83;
  HEADING_2 = 84;
  HEADING_3 = 85;
  HEADING_4 = 86;
  HEADING_5 = 87;
  HEADING_6 = 88;
  HEADER = 89;
  FOOTER = 90;
  SIDEBAR = 91;
  SECTION = 92;
  MAINCONTENT = 93;
  TABLE_CELL = 94;
  WIDGET = 95;
}

message MigrationStatus {
  bool dsdCleanup = 1;
}

message NodeFieldMap {
  NodeFieldMapEntry[] entries = 1;
}

message NodeFieldMapEntry {
  GUID guid = 1;
  uint field = 2;
  uint lastModifiedSequenceNumber = 3;
}

enum ColorProfile {
  SRGB = 0;
  DISPLAY_P3 = 1;
}

enum DocumentColorProfile {
  LEGACY = 0;
  SRGB = 1;
  DISPLAY_P3 = 2;
}

enum ChildReadingDirection {
  NONE = 0;
  LEFT_TO_RIGHT = 1;
  RIGHT_TO_LEFT = 2;
}

message ARIAAttributeAnyValue {
  bool boolValue = 1;
  string stringValue = 2;
  float floatValue = 3;
  int intValue = 4;
  string[] stringArrayValue = 5;
}

enum ARIAAttributeDataType {
  BOOLEAN = 0;
  STRING = 1;
  FLOAT = 2;
  INT = 3;
  STRING_LIST = 4;
}

message ARIAAttributeData {
  ARIAAttributeDataType type = 1;
  ARIAAttributeAnyValue value = 2;
}

message ARIAAttributesMap {
  ARIAAttributesMapEntry[] entries = 1;
}

message ARIAAttributesMapEntry {
  string attribute = 1;
  ARIAAttributeData value = 2;
}

message HandoffStatusMapEntry {
  GUID guid = 1;
  SectionStatusInfo handoffStatus = 2;
}

message HandoffStatusMap {
  HandoffStatusMapEntry[] entries = 1;
}

message EditScopeInfo {
  EditScopeStack[] editScopeStacks = 1;
  EditScopeSnapshot[] snapshots = 2;
}

message EditScopeSnapshot {
  EditScopeStack[] frames = 1;
  uint[] nodeChangeFieldNumbers = 2;
}

message EditScopeStack {
  EditScope[] stack = 1;
}

message EditScope {
  EditScopeType type = 1;
  string label = 2;
  EditorType editorType = 3;
}

enum EditScopeType {
  INVALID = 0;
  TEST_SETUP = 1;
  USER = 2;
  PLUGIN = 3;
  SYSTEM = 4;
  REST_API = 5;
  ONBOARDING = 6;
  AUTOSAVE = 7;
  AI = 8;
}

enum SectionPresetState {
  INSERTED = 0;
  USER_EDITED = 1;
}

enum EmojiImageSet {
  APPLE = 0;
  NOTO = 1;
}

enum SelectionRegionFocusType {
  NONE = 0;
  PRIMARY = 1;
  SECONDARY = 2;
}

message SectionPresetInfo {
  uint64 shelfId = 1;
  uint64 templateId = 2;
  string templateName = 3;
  SectionPresetState state = 4;
}

message ClipboardSelectionRegion {
  GUID parent = 1;
  GUID[] nodes = 2;
  Vector enclosingFrameOffset = 3;
  bool pasteIsPartiallyOutsideEnclosingFrame = 4;
  SelectionRegionFocusType focusType = 5;
}

enum FirstDraftKitType {
  LOCAL = 0;
  LIBRARY = 1;
  NONE = 2;
}

message FirstDraftKit {
  string key = 1;
  FirstDraftKitType type = 2;
}

message FirstDraftData {
  string generationId = 1;
  FirstDraftKit kit = 2;
}

enum FirstDraftKitElementType {
  NONE = 0;
  BUILDING_BLOCK = 1;
  GROUPED_COMPONENT = 2;
}

message FirstDraftKitElementData {
  FirstDraftKitElementType type = 1;
}

enum PlatformShapeProperty {
  FILL = 0;
  STROKE = 1;
  TEXT = 2;
  STROKE_COLOR = 3;
}

enum PlatformShapeBehaviorType {
  SHAPE = 0;
  CONTAINER = 1;
  ADVANCED_CONTAINER = 2;
}

message PlatformShapePropertyMapEntry {
  PlatformShapeProperty property = 1;
  GUIDPath[] nodePaths = 2;
}

message PlatformShapeDefinition {
  PlatformShapePropertyMapEntry[] propertyMapEntries = 1;
  PlatformShapeBehaviorType behaviorType = 2;
  GUIDPath thumbnailNode = 3;
}

message NodeBehaviors {
  LinkBehavior link = 1;
  AppearBehavior appear = 2;
  HoverBehavior hover = 3;
  PressBehavior press = 4;
  FocusBehavior focus = 5;
  ScrollParallaxBehavior scrollParallax = 6;
  ScrollTransformBehavior scrollTransform = 7;
  CursorBehavior cursor = 8;
  MarqueeBehavior marquee = 9;
  CodeBehavior[] code = 10;
}

message BehaviorTransition {
  EasingType easingType = 1;
  float[] easingFunction = 2;
  float transitionDuration = 3;
  float delay = 4;
  VariableData transitionDurationVar = 5;
  VariableData delayVar = 6;
}

enum AppearBehaviorTrigger {
  PAGE_LOAD = 1;
  THIS_LAYER_IN_VIEW = 2;
  OTHER_LAYER_IN_VIEW = 3;
  SCROLL_DIRECTION = 4;
}

enum RelativeDirection {
  UP = 1;
  DOWN = 2;
  LEFT = 3;
  RIGHT = 4;
}

message AppearBehavior {
  AppearBehaviorTrigger trigger = 1;
  RelativeDirection direction = 2;
  GUID otherLayer = 3;
  BehaviorTransition enterTransition = 4;
  NodeChange enterState = 5;
  BehaviorTransition exitTransition = 6;
  NodeChange exitState = 7;
  bool playsOnce = 8;
  VariableData playsOnceVar = 9;
  bool isDeleted = 10;
}

message HoverBehavior {
  BehaviorTransition transition = 1;
  NodeChange state = 2;
  bool isDeleted = 3;
}

message PressBehavior {
  BehaviorTransition transition = 1;
  NodeChange state = 2;
  bool isDeleted = 3;
}

message FocusBehavior {
  BehaviorTransition transition = 1;
  NodeChange state = 2;
  bool isDeleted = 3;
}

message ScrollParallaxBehavior {
  ScrollDirection axis = 1;
  float speed = 2;
  bool relativeToPage = 3;
  VariableData speedVar = 4;
  bool isDeleted = 5;
}

enum ScrollTransformBehaviorTrigger {
  PAGE_HEIGHT = 1;
  THIS_LAYER_IN_VIEW = 2;
  OTHER_LAYER_IN_VIEW = 3;
}

message ScrollTransformBehavior {
  ScrollTransformBehaviorTrigger trigger = 1;
  GUID otherLayer = 2;
  BehaviorTransition transition = 3;
  NodeChange fromState = 4;
  NodeChange toState = 5;
  bool playsOnce = 6;
  bool playsOnceVar = 7;
  VariableData playsOnceVar2 = 8;
  bool isDeleted = 9;
}

message CursorBehavior {
  float hotspotX = 1;
  float hotspotY = 2;
  GUID cursorGuid = 3;
  bool isDeleted = 4;
}

message MarqueeBehavior {
  RelativeDirection direction = 1;
  float speed = 2;
  bool shouldLoopInfinitely = 3;
  VariableData speedVar = 4;
  VariableData shouldLoopInfinitelyVar = 5;
  VariableData pauseOnHover = 6;
  bool isDeleted = 7;
}

message CodeBehavior {
  CodeComponentId codeComponentId = 1;
  ComponentPropAssignment[] componentPropAssignments = 2;
  bool isDeleted = 3;
}

message ClientRenderedMetadata {
  string loadID = 1;
  string trackingSessionId = 2;
  uint trackingSessionSequenceId = 3;
  string reconnectID = 4;
}

enum LinkBehaviorType {
  URL = 1;
  PAGE = 2;
}

message LinkBehavior {
  LinkBehaviorType type = 1;
  string url = 2;
  GUID page = 3;
  bool openInNewWindow = 4;
}

message VariableIdOrVariableOverrideId {
  VariableID variableId = 1;
  VariableOverrideId variableOverrideId = 2;
}

struct IndexFontVariationAxis {
  string tag;
  string name;
  float min;
  float max;
  float defaultValue;
}

struct IndexFontVariationAxisValue {
  string tag;
  float value;
}

message IndexFontStyle {
  string name = 1;
  string postscript = 2;
  float weight = 3;
  bool italic = 4;
  float stretch = 5;
  IndexFontVariationAxisValue[] variationAxisValues = 6;
}

message IndexFontFile {
  string filename = 1;
  uint version = 2;
  string family = 3;
  IndexFontStyle[] styles = 4;
  IndexFontVariationAxis[] variationAxes = 5;
  bool useFontOpticalSize = 6;
}

struct IndexFamilyRename {
  string oldFamily;
  string newFamily;
}

struct IndexStyleRename {
  string oldStyle;
  string newStyle;
}

struct IndexFamilyStylesRename {
  string familyName;
  IndexStyleRename[] styleRenames;
}

struct IndexRenames {
  IndexFamilyRename[] family;
  IndexFamilyStylesRename[] style;
}

struct IndexEmojiSequence {
  uint[] codepoints;
}

struct IndexEmojis {
  uint revision;
  uint[] sizes;
  IndexEmojiSequence[] sequences;
}

message FontIndex {
  uint schemaVersion = 1;
  IndexFontFile[] files = 2;
  IndexRenames renames = 3;
  IndexEmojis emojis = 4;
}

message SlideThemeData {
  ThemeID themeID = 1;
  string version = 2;
}

enum SlideNumber {
  NONE = 0;
  SLIDE = 1;
  SECTION = 2;
  SUBSECTION = 3;
  TOTAL_WITHIN_DECK = 4;
  TOTAL_WITHIN_SECTION = 5;
}

enum NodeChatMessageType {
  USER_MESSAGE = 0;
  ASSISTANT_MESSAGE = 1;
  TOOL_MESSAGE = 2;
  SYSTEM_MESSAGE = 3;
}

message NodeChatMessage {
  GUID id = 1;
  NodeChatMessageType type = 2;
  string userId = 3;
  string textContent = 4;
  uint sentAt = 5;
  NodeChatToolCall[] toolCalls = 6;
  NodeChatToolResult[] toolResults = 7;
  uint64 sentAt64 = 8;
}

message NodeChatToolCall {
  string toolCallId = 1;
  string toolName = 2;
  string argsJson = 3;
}

message NodeChatToolResult {
  string toolCallId = 1;
  string toolName = 2;
  string resultJson = 3;
}

message NodeChatExchange {
  GUID node = 1;
  NodeChatMessage[] messages = 2;
  bool isTyping = 3;
  FileUpdate[] fileUpdates = 4;
}

message NodeChatCompressionState {
  uint startIndex = 1;
  string summary = 2;
}

message FileUpdate {
  string name = 1;
  string contents = 2;
  bool isDeleted = 3;
}

message AIChatContentPart {
  AIChatContentPartType type = 1;
  AIChatContentPartAnyValue value = 2;
}

enum AIChatContentPartType {
  INVALID = 0;
  TEXT = 1;
  SELECTED_NODE_IDS = 2;
}

message AIChatContentPartAnyValue {
  string textValue = 1;
  string[] selectedNodeIds = 2;
}

enum AIChatMessageRole {
  USER = 0;
  ASSISTANT = 1;
  TOOL = 2;
  SYSTEM = 3;
}

message AIChatMessage {
  uint createdAtMs = 1;
  AIChatMessageRole role = 2;
  AIChatContentPart[] content = 3;
  string clientId = 4;
  uint64 createdAtMs64 = 5;
}

message AIChatThread {
  AIChatMessage[] messages = 1;
}

enum CooperTemplateType {
  CUSTOM = 0;
  TWITTER_POST = 1;
  LINKEDIN_POST = 2;
  INSTA_POST_SQUARE = 3;
  INSTA_POST_PORTRAIT = 4;
  INSTA_STORY = 5;
  INSTA_AD = 6;
  FACEBOOK_POST = 7;
  FACEBOOK_COVER_PHOTO = 8;
  FACEBOOK_EVENT_COVER = 9;
  FACEBOOK_AD_PORTRAIT = 10;
  FACEBOOK_AD_SQUARE = 11;
  PINTEREST_AD_PIN = 12;
  TWITTER_BANNER = 13;
  LINKEDIN_POST_SQUARE = 15;
  LINKEDIN_POST_PORTRAIT = 16;
  LINKEDIN_POST_LANDSCAPE = 17;
  LINKEDIN_PROFILE_BANNER = 18;
  LINKEDIN_ARTICLE_BANNER = 19;
  LINKEDIN_AD_LANDSCAPE = 20;
  LINKEDIN_AD_SQUARE = 21;
  LINKEDIN_AD_VERTICAL = 22;
  YOUTUBE_THUMBNAIL = 23;
  YOUTUBE_BANNER = 24;
  YOUTUBE_AD = 25;
  TWITCH_BANNER = 26;
  GOOGLE_LEADERBOARD_AD = 27;
  GOOGLE_LARGE_AD = 28;
  GOOGLE_MED_AD = 29;
  GOOGLE_MOBILE_BANNER_AD = 30;
  GOOGLE_SKYSCRAPER_AD = 31;
  CARD_HORIZONTAL = 32;
  CARD_VERTICAL = 33;
  PRINT_US_LETTER = 34;
  POSTER = 35;
  BANNER_STANDARD = 36;
  BANNER_WIDE = 37;
  BANNER_ULTRAWIDE = 38;
  NAME_TAG_PORTRAIT = 39;
  NAME_TAG_LANDSCAPE = 40;
  INSTA_REEL_COVER = 41;
  ZOOM_BACKGROUND = 42;
  TIKTOK_POST = 43;
  INSTA_AD_PORTRAIT = 44;
  INSTA_POST_TALL_PORTRAIT = 45;
  TWITTER_POST_SQUARE = 46;
  FACEBOOK_POST_SQUARE = 47;
  FACEBOOK_POST_PORTRAIT = 48;
  FACEBOOK_STORY = 49;
  GOOGLE_SQUARE_AD = 50;
  GOOGLE_SMALL_SQUARE_AD = 51;
  GOOGLE_NARROW_SKYSCRAPER_AD = 52;
  GOOGLE_HALF_PAGE_AD = 53;
  GOOGLE_LARGE_LEADERBOARD_AD = 54;
  GOOGLE_BILLBOARD_AD = 55;
  GOOGLE_BANNER_LEADERBOARD_AD = 56;
  GOOGLE_TOP_BANNER_AD = 57;
  GOOGLE_MOBILE_LEADERBOARD_BANNER_AD = 58;
  GOOGLE_LARGE_MOBILE_BANNER_AD = 59;
  GOOGLE_MOBILE_INTERSTITIAL_AD = 60;
  GOOGLE_MOBILE_MED_RECTANGLE_AD = 61;
  PINTEREST_PIN_STANDARD = 62;
  PINTEREST_PIN_SQUARE = 63;
  PINTEREST_AD_SQUARE = 64;
  PRINT_A4 = 65;
}

message CooperTemplateData {
  CooperTemplateType type = 1;
}

message ImageImportMap {
  ImageImport[] imports = 1;
}

message ImageImport {
  string name = 1;
  Image image = 2;
}

enum InterpolationType {
  HOLD = 0;
  BEZIER = 1;
  SPRING = 2;
}

message BezierHandles {
  float p1x = 1;
  float p1y = 2;
  float p2x = 3;
  float p2y = 4;
}

enum KeyframeOperation {
  SET = 0;
  SCALE = 1;
  OFFSET = 2;
}

enum TimelinePositionType {
  ABSOLUTE = 0;
  RELATIVE = 1;
}

enum PlaybackStyle {
  ONCE = 0;
  LOOP = 1;
  BOOMERANG = 2;
}
`);pt(zi),new Set((zi.definitions.find(e=>e.name===`OpenTypeFeature`)?.fields??[]).map(e=>e.name));let Bi=[[`fontVariantCommonLigatures`,`LIGA`],[`fontVariantContextualLigatures`,`CALT`],[`fontVariantDiscretionaryLigatures`,`DLIG`],[`fontVariantHistoricalLigatures`,`HLIG`],[`fontVariantOrdinal`,`ORDN`],[`fontVariantSlashedZero`,`ZERO`]],Vi=[[`fontVariantNumericFigure`,{LINING:`LNUM`,OLDSTYLE:`ONUM`}],[`fontVariantNumericSpacing`,{PROPORTIONAL:`PNUM`,TABULAR:`TNUM`}],[`fontVariantNumericFraction`,{DIAGONAL:`FRAC`,STACKED:`AFRC`}],[`fontVariantCaps`,{SMALL:`SMCP`,PETITE:`PCAP`,ALL_SMALL:[`SMCP`,`C2SC`],ALL_PETITE:[`PCAP`,`C2PC`],UNICASE:`UNIC`,TITLING:`TITL`}]];Object.fromEntries(Bi.map(([e,t])=>[t,e]));function Hi(e,t,n){let r=t.toUpperCase();e.some(e=>e.tag===r)||e.push({tag:r,enabled:n})}function Ui(e){let t=[];for(let[n,r]of Bi){let i=e[n];i!==void 0&&Hi(t,r,i)}for(let[n,r]of Vi){let i=r[String(e[n])];if(Array.isArray(i))for(let e of i)Hi(t,e,!0);else i&&Hi(t,i,!0)}for(let n of e.toggledOnOTFeatures??[])Hi(t,n,!0);for(let n of e.toggledOffOTFeatures??[])Hi(t,n,!1);return t}function Wi(e){return String.fromCharCode(e>>24&255,e>>16&255,e>>8&255,e&255)}function Gi(e){let t=[];for(let n of e.fontVariations??[]){if(typeof n.value!=`number`)continue;let e=typeof n.axisTag==`number`?Wi(n.axisTag):n.axisName||``;e&&t.push({axis:e,value:n.value})}return t}function Ki(e){return e?{r:e.r??0,g:e.g??0,b:e.b??0,a:e.a??1}:{...dn}}function qi(e){return Object.keys(e).sort((e,t)=>Number(e)-Number(t)).map(t=>e[Number(t)]).map(e=>e.toString(16).padStart(2,`0`)).join(``)}function Ji(e){if(e)return{m00:e.m00,m01:e.m01,m02:e.m02,m10:e.m10,m11:e.m11,m12:e.m12}}let Yi=null;function Xi(e){Yi=e}function Zi(e){let t=e.colorVar?.value?.alias;if(!(!t||!Yi))return Yi(t)??void 0}function Qi(e){let t=Zi(e);return t?{color:{...t,a:e.color?.a??1},opacity:e.opacity??t.a}:{color:Ki(e.color),opacity:e.opacity??1}}function $i(e){let{color:t,opacity:n}=Qi(e);return{type:e.type,color:t,opacity:n,visible:e.visible??!0,blendMode:e.blendMode??`NORMAL`}}function ea(e,t){!t.type.startsWith(`GRADIENT`)||!t.stops||(e.gradientStops=t.stops.map(e=>({color:Ki(e.color),position:e.position})),t.transform&&(e.gradientTransform=Ji(t.transform)))}function ta(e,t){if(t.type===`IMAGE`){if(t.image&&typeof t.image==`object`){let n=t.image;typeof n.hash==`object`?e.imageHash=qi(n.hash):typeof n.hash==`string`&&(e.imageHash=n.hash)}e.imageScaleMode=t.imageScaleMode??`FILL`,t.transform&&(e.imageTransform=Ji(t.transform))}}function na(e,t){t.sourceNodeId&&(e.sourceNodeId=J(t.sourceNodeId)),t.scale&&(e.scale=t.scale),t.spacing&&(e.spacing=t.spacing),t.patternSpacing&&(e.patternSpacing=t.patternSpacing),t.patternTileType&&(e.patternTileType=t.patternTileType),t.verticalAlignment&&(e.verticalAlignment=t.verticalAlignment),t.horizontalAlignment&&(e.horizontalAlignment=t.horizontalAlignment),t.noiseType&&(e.noiseType=t.noiseType),t.density!==void 0&&(e.density=t.density),t.noiseSize&&(e.noiseSize=t.noiseSize),t.customEffectId?.guid&&(e.customEffectId=J(t.customEffectId.guid))}function ra(e){return e?e.map(e=>{let t=$i(e);return ea(t,e),ta(t,e),na(t,e),t}):[]}function ia(e,t,n,r,i,a){if(!e)return[];let o=`CENTER`;return n===`INSIDE`?o=`INSIDE`:n===`OUTSIDE`&&(o=`OUTSIDE`),e.map(e=>{let{color:n,opacity:s}=Qi(e);return{color:n,weight:t??1,opacity:s,visible:e.visible??!0,align:o,cap:r??`NONE`,join:i??`MITER`,dashPattern:a??[]}})}function aa(e){return e?e.map(e=>({type:e.type,color:Ki(e.color),offset:e.offset??{x:0,y:0},radius:e.radius??0,spread:e.spread??0,visible:e.visible??!0,blendMode:e.blendMode??`NORMAL`,showShadowBehindNode:e.showShadowBehindNode??!0})):[]}function oa(e,t,n){return t===0&&n===0?e:An(e,1,0,0,1,t,n)}function sa(e,t,n){let r=[...e.fillGeometry??[],...e.strokeGeometry??[]],i=r.length>0?un(r):null,a=i?.x??0,o=i?.y??0,s=i?i.x+i.width:t,c=i?i.y+i.height:n;for(let t of e.derivedTextGlyphs??[]){let e=t.fontSize||0;a=Math.min(a,t.x-e*.25),o=Math.min(o,t.y-e),s=Math.max(s,t.x+e),c=Math.max(c,t.y+e*.35)}return{left:Math.max(0,-a+ +(a<0)),top:Math.max(0,-o+ +(o<0)),right:Math.max(0,s-t+ +(s>t)),bottom:Math.max(0,c-n+ +(c>n))}}function ca(e,t,n){e.strokeGeometry&&e.strokeGeometry.length>0&&(e.strokeGeometry=oa(e.strokeGeometry,t,n)),e.fillGeometry&&e.fillGeometry.length>0&&(e.fillGeometry=oa(e.fillGeometry,t,n)),e.derivedTextGlyphs?.length&&(e.derivedTextGlyphs=e.derivedTextGlyphs.map(e=>({...e,x:e.x+t,y:e.y+n})))}function la(e,t){if(e.nodeType!==`TEXT`||!t)return;e.textPathData=t;let n=e.width??0,r=e.height??0;e.textPathBox={x:0,y:0,width:n,height:r};let i=sa(e,n,r);if(i.left===0&&i.top===0&&i.right===0&&i.bottom===0)return;let a=i.left,o=i.top;e.x=(e.x??0)-a,e.y=(e.y??0)-o,e.width=n+i.left+i.right,e.height=r+i.top+i.bottom,(a!==0||o!==0)&&(e.textPathBox={x:a,y:o,width:n,height:r},ca(e,a,o))}let ua=Object.fromEntries(Object.entries({cornerRadius:`CORNER_RADIUS`,topLeftRadius:`RECTANGLE_TOP_LEFT_CORNER_RADIUS`,topRightRadius:`RECTANGLE_TOP_RIGHT_CORNER_RADIUS`,bottomLeftRadius:`RECTANGLE_BOTTOM_LEFT_CORNER_RADIUS`,bottomRightRadius:`RECTANGLE_BOTTOM_RIGHT_CORNER_RADIUS`,strokeWeight:`STROKE_WEIGHT`,borderTopWeight:`BORDER_TOP_WEIGHT`,borderBottomWeight:`BORDER_BOTTOM_WEIGHT`,borderLeftWeight:`BORDER_LEFT_WEIGHT`,borderRightWeight:`BORDER_RIGHT_WEIGHT`,itemSpacing:`STACK_SPACING`,paddingLeft:`STACK_PADDING_LEFT`,paddingTop:`STACK_PADDING_TOP`,paddingRight:`STACK_PADDING_RIGHT`,paddingBottom:`STACK_PADDING_BOTTOM`,counterAxisSpacing:`STACK_COUNTER_SPACING`,gridRowGap:`GRID_ROW_GAP`,gridColumnGap:`GRID_COLUMN_GAP`,visible:`VISIBLE`,opacity:`OPACITY`,width:`WIDTH`,height:`HEIGHT`,minWidth:`MIN_WIDTH`,maxWidth:`MAX_WIDTH`,minHeight:`MIN_HEIGHT`,maxHeight:`MAX_HEIGHT`,x:`X_POSITION`,y:`Y_POSITION`,rotation:`ROTATION`,fontSize:`FONT_SIZE`,letterSpacing:`LETTER_SPACING`,lineHeight:`LINE_HEIGHT`,fontFamily:`FONT_FAMILY`}).map(([e,t])=>[t,e]));function da(e){let t=e.variableField?ua[e.variableField]:void 0,n=e.variableData?.value?.alias?.guid;return t&&n?{field:t,variableId:J(n)}:void 0}let fa=new Set(`cornerRadius.topLeftRadius.topRightRadius.bottomLeftRadius.bottomRightRadius.strokeWeight.borderTopWeight.borderBottomWeight.borderLeftWeight.borderRightWeight.itemSpacing.paddingLeft.paddingTop.paddingRight.paddingBottom.counterAxisSpacing.gridRowGap.gridColumnGap.width.height.minWidth.maxWidth.minHeight.maxHeight.x.y.rotation.fontSize.letterSpacing.lineHeight`.split(`.`));function pa(e,t){return e===`opacity`?{opacity:Math.max(0,Math.min(1,t/100))}:fa.has(e)?{[e]:t}:void 0}let ma={PNG:`png`,JPEG:`jpg`,SVG:`svg`,PDF:`pdf`};function ha(e){let t=Ta(e,`textPathBox`);if(!t)return null;try{let e=JSON.parse(t);if(!e||typeof e!=`object`)return null;let{x:n,y:r,width:i,height:a}=e;return typeof n!=`number`||typeof r!=`number`||typeof i!=`number`||typeof a!=`number`||!Number.isFinite(n+r+i+a)||i<=0||a<=0?null:{x:n,y:r,width:i,height:a}}catch{return null}}function ga(e){if(!e)return{};try{let t=JSON.parse(e);return!t||typeof t!=`object`||Array.isArray(t)?{}:Object.fromEntries(Object.entries(t).filter(e=>typeof e[0]==`string`&&typeof e[1]==`string`))}catch{return{}}}function _a(e){let t=ga(Ta(e,`boundVariables`));for(let n of e.variableConsumptionMap?.entries??[]){let e=da(n);e&&(t[e.field]=e.variableId)}return e.fillPaints?.forEach((e,n)=>{let r=e.colorVariableBinding?.variableID??e.colorVar?.value?.alias?.guid;r&&(t[`fills/${n}/color`]=J(r))}),e.strokePaints?.forEach((e,n)=>{let r=e.colorVariableBinding?.variableID??e.colorVar?.value?.alias?.guid;r&&(t[`strokes/${n}/color`]=J(r))}),t}function va(e){return e===`png`||e===`jpg`||e===`webp`||e===`svg`||e===`pdf`}function ya(e){if(!e)return null;try{let t=JSON.parse(e);if(!Array.isArray(t))return null;let n=t.flatMap(e=>{if(!e||typeof e!=`object`||Array.isArray(e))return[];let t=e.scale,n=e.format;return typeof t!=`number`||!Number.isFinite(t)||!va(n)?[]:[{scale:gr(t),format:n}]});return n.length===t.length?n:null}catch{return null}}function ba(e){return typeof e==`string`?ma[e]??null:e===0?`png`:e===1?`jpg`:e===2?`svg`:e===3?`pdf`:null}function xa(e){if(!e||typeof e!=`object`||Array.isArray(e))return 1;let t=e.type;if(t!==`CONTENT_SCALE`&&t!==0)return 1;let n=e.value;return typeof n==`number`&&Number.isFinite(n)?gr(n):1}function Sa(e){return ya(Ta(e,`exportSettings`))||(e.exportSettings??[]).flatMap(e=>{if(!e||typeof e!=`object`||Array.isArray(e))return[];let t=ba(e.imageType);return t?[{scale:xa(e.constraint),format:t}]:[]})}function Ca(e){return(e.pluginData??[]).map(e=>({pluginId:e.pluginID,key:e.key,value:e.value}))}function wa(e){let t=Ta(e,`librarySource`);if(!t)return null;try{let e=JSON.parse(t);if(!e||typeof e!=`object`||Array.isArray(e))return null;let n=e;return typeof n.identity?.libraryId!=`string`||typeof n.identity.assetKey!=`string`||typeof n.identity.revisionId!=`string`?null:{identity:{libraryId:n.identity.libraryId,assetKey:n.identity.assetKey,revisionId:n.identity.revisionId},sourceNodeId:typeof n.sourceNodeId==`string`?n.sourceNodeId:null,readOnly:n.readOnly===!0}}catch{return null}}function Ta(e,t){return e.pluginData?.find(e=>e.pluginID===`open-pencil`&&e.key===t)?.value??null}function Ea(e){return(e.pluginRelaunchData??[]).map(e=>({pluginId:e.pluginID,command:e.command,message:e.message,isDeleted:e.isDeleted}))}function Da(e){switch(e){case`UNDERLINE`:return`UNDERLINE`;case`STRIKETHROUGH`:return`STRIKETHROUGH`;default:return`NONE`}}function Oa(e,t){return e?e.units===`PIXELS`?e.value:e.units===`PERCENT`?e.value/100*(t??14):e.units===`RAW`?e.value*(t??14):null:null}function ka(e,t){return e?e.units===`PIXELS`?e.value:e.units===`PERCENT`?e.value/100*(t??14):e.value:0}function Aa(e,t){let n=t.textDecoration;if(n&&(e.textDecoration=Da(n)),t.textDecorationStyle&&(e.textDecorationStyle=t.textDecorationStyle),t.textDecorationThickness&&(e.textDecorationThickness=t.textDecorationThickness.value??null),t.textDecorationSkipInk!==void 0&&(e.textDecorationSkipInk=t.textDecorationSkipInk),t.textUnderlineOffset&&(e.textUnderlineOffset=t.textUnderlineOffset.value??null),t.textDecorationFillPaints){let n=ra(t.textDecorationFillPaints);n.length>0&&(e.textDecorationFills=n)}}function ja(e,t){let n={};e.fontName&&(n.fontFamily=e.fontName.family,n.fontWeight=Mr(e.fontName.style),n.italic=e.fontName.style.toLowerCase().includes(`italic`)),e.fontSize!==void 0&&(n.fontSize=e.fontSize);let r=Gi(e);r.length>0&&(n.fontVariations=r);let i=Ui(e);if(i.length>0&&(n.fontFeatures=i),e.letterSpacing&&(n.letterSpacing=ka(e.letterSpacing,e.fontSize??t)),e.lineHeight){let r=Oa(e.lineHeight,e.fontSize??t);r!=null&&(n.lineHeight=r)}if(Aa(n,e),e.fillPaints){let t=ra(e.fillPaints);t.length>0&&(n.fills=t)}return n}function Ma(e,t){let n=new Map;for(let r of e){let e=r.styleID;if(e===void 0)continue;let i=ja(r,t);Object.keys(i).length>0&&n.set(e,i)}return n}function Na(e,t){let n=[],r=e[0],i=0;for(let a=1;a<=e.length;a++)if(a===e.length||e[a]!==r){if(r!==0){let e=t.get(r);e&&n.push({start:i,length:a-i,style:e})}a<e.length&&(r=e[a],i=a)}return n}function Pa(e){let t=e.textData;if(!t?.characterStyleIDs||!t.styleOverrideTable)return[];let n=t.characterStyleIDs;if(n.length===0||t.styleOverrideTable.length===0)return[];let r=Ma(t.styleOverrideTable,e.fontSize);return r.size===0?[]:Na(n,r)}function Fa(e,t){let n=new DataView(e.buffer,e.byteOffset,e.byteLength),r=0,i=n.getUint32(r,!0);r+=4;let a=n.getUint32(r,!0);r+=4;let o=n.getUint32(r,!0);r+=4;let s=new Map;for(let e of t??[])s.set(e.styleID,e);let c=[];for(let e=0;e<i;e++){let e=n.getUint32(r,!0);r+=4;let t=n.getFloat32(r,!0);r+=4;let i=n.getFloat32(r,!0);r+=4;let a=s.get(e),o={x:t,y:i,handleMirroring:a?.handleMirroring??`NONE`};a?.strokeCap&&(o.strokeCap=a.strokeCap),c.push(o)}let l=[];for(let e=0;e<a;e++){r+=4;let e=n.getUint32(r,!0);r+=4;let t=n.getFloat32(r,!0);r+=4;let i=n.getFloat32(r,!0);r+=4;let a=n.getUint32(r,!0);r+=4;let o=n.getFloat32(r,!0);r+=4;let s=n.getFloat32(r,!0);r+=4,l.push({start:e,end:a,tangentStart:{x:t,y:i},tangentEnd:{x:o,y:s}})}let u=[];for(let e=0;e<o;e++){let e=n.getUint32(r,!0)===0?`EVENODD`:`NONZERO`;r+=4;let t=n.getUint32(r,!0);r+=4;let i=[];for(let e=0;e<t;e++){let e=n.getUint32(r,!0);r+=4;let t=[];for(let i=0;i<e;i++)t.push(n.getUint32(r,!0)),r+=4;i.push(t)}u.push({windingRule:e,loops:i})}return{vertices:c,segments:l,regions:u}}function Ia(e,t){let n=t?.regions??[];return e.length===n.length?e.map((e,t)=>({...e,windingRule:n[t].windingRule})):e.length===1&&n.length>0&&n.every(e=>e.windingRule===n[0].windingRule)?[{...e[0],windingRule:n[0].windingRule}]:e}function La(e,t){let n=e.vectorData;if(n?.vectorNetworkBlob===void 0)return null;let r=n.vectorNetworkBlob;if(r<0||r>=t.length)return null;try{let i=Fa(t[r],n.styleOverrideTable),a=n.normalizedSize,o=e.size?.x??0,s=e.size?.y??0;if(a&&o>0&&s>0&&(a.x!==o||a.y!==s)){let e=o/a.x,t=s/a.y;for(let n of i.vertices)n.x*=e,n.y*=t;for(let n of i.segments)n.tangentStart={x:n.tangentStart.x*e,y:n.tangentStart.y*t},n.tangentEnd={x:n.tangentEnd.x*e,y:n.tangentEnd.y*t}}return i}catch{return null}}function Ra(e){let t=new Map;for(let n of e??[])n.fillPaints&&n.fillPaints.length>0&&t.set(n.styleID,ra(n.fillPaints));return t}function za(e){let t=e.vectorData;return Ra(t?.styleOverrideTable)}function Ba(e,t,n){if(!e||e.length===0)return[];let r=[];for(let i of e){if(i.commandsBlob===void 0||i.commandsBlob<0||i.commandsBlob>=t.length)continue;let e=t[i.commandsBlob];if(e.length===0)continue;let a=i.styleID?n?.get(i.styleID):void 0;r.push({windingRule:i.windingRule===`EVENODD`?`EVENODD`:`NONZERO`,commandsBlob:e,fills:a&&a.length>0?X(a):void 0})}return r}function Va(e){let t={},n=e.variableModeBySetMap;for(let e of n?.entries??[]){let n=e.variableSetID?.guid,r=e.variableModeID;!n||!r||(t[J(n)]=J(r))}return t}let Ha={DOCUMENT:`DOCUMENT`,VARIABLE:`VARIABLE`,CANVAS:`CANVAS`,FRAME:`FRAME`,RECTANGLE:`RECTANGLE`,ROUNDED_RECTANGLE:`ROUNDED_RECTANGLE`,ELLIPSE:`ELLIPSE`,TEXT:`TEXT`,LINE:`LINE`,STAR:`STAR`,REGULAR_POLYGON:`POLYGON`,VECTOR:`VECTOR`,BOOLEAN_OPERATION:`BOOLEAN_OPERATION`,GROUP:`GROUP`,SECTION:`SECTION`,COMPONENT:`COMPONENT`,COMPONENT_SET:`COMPONENT_SET`,INSTANCE:`INSTANCE`,SYMBOL:`COMPONENT`,CONNECTOR:`CONNECTOR`,SHAPE_WITH_TEXT:`SHAPE_WITH_TEXT`,TEXT_PATH:`TEXT`};function Ua(e){return e?Ha[e]??`RECTANGLE`:`RECTANGLE`}function Wa(e){if(e.type!==`BOOLEAN_OPERATION`)return;let t=e.booleanOperation;switch(t){case`SUBTRACT`:case`INTERSECT`:return t;case`EXCLUDE`:case`XOR`:return`EXCLUDE`;default:return`UNION`}}function Ga(e){switch(e){case`HORIZONTAL`:return`HORIZONTAL`;case`VERTICAL`:return`VERTICAL`;default:return`NONE`}}function Ka(e){switch(e){case`RESIZE_TO_FIT`:case`RESIZE_TO_FIT_WITH_IMPLICIT_SIZE`:return`HUG`;case`FILL`:return`FILL`;default:return`FIXED`}}function qa(e){switch(e){case`CENTER`:return`CENTER`;case`MAX`:return`MAX`;case`SPACE_BETWEEN`:case`SPACE_EVENLY`:return`SPACE_BETWEEN`;default:return`MIN`}}function Ja(e){switch(e){case`CENTER`:return`CENTER`;case`MAX`:return`MAX`;case`STRETCH`:return`STRETCH`;case`BASELINE`:return`BASELINE`;default:return`MIN`}}function Ya(e){switch(e){case`MIN`:return`MIN`;case`CENTER`:return`CENTER`;case`MAX`:return`MAX`;case`STRETCH`:return`STRETCH`;case`BASELINE`:return`BASELINE`;default:return`AUTO`}}function Xa(e){switch(e){case`CENTER`:return`CENTER`;case`MAX`:return`MAX`;case`STRETCH`:return`STRETCH`;case`SCALE`:return`SCALE`;default:return`MIN`}}function Za(e){return e?{startingAngle:e.startingAngle??0,endingAngle:e.endingAngle??2*Math.PI,innerRadius:e.innerRadius??0}:null}function Qa(e){let t=e.size?.x??100,n=e.size?.y??100,r=e.transform?.m02??0,i=e.transform?.m12??0,a=0,o=!1;if(e.transform){let s=e.transform;s.m00*s.m11-s.m01*s.m10<0&&(o=!0),a=Math.atan2(s.m10,o?s.m11:s.m00)*(180/Math.PI);let c=a*Math.PI/180,l=Math.cos(c),u=Math.sin(c),d=t/2,f=n/2,p=o?-l:l,m=o?u:-u,h=u,g=l;r=s.m02-d+p*d+m*f,i=s.m12-f+h*d+g*f}return{x:r,y:i,width:t,height:n,rotation:a,flipX:o,flipY:!1}}function $a(e){return{cornerRadius:e.cornerRadius??0,topLeftRadius:e.rectangleTopLeftCornerRadius??e.cornerRadius??0,topRightRadius:e.rectangleTopRightCornerRadius??e.cornerRadius??0,bottomRightRadius:e.rectangleBottomRightCornerRadius??e.cornerRadius??0,bottomLeftRadius:e.rectangleBottomLeftCornerRadius??e.cornerRadius??0,independentCorners:e.rectangleCornerRadiiIndependent??!1,cornerSmoothing:e.cornerSmoothing??0}}function eo(e){let t=e.derivedTextData?.baselines?.[0]?.lineHeight;return t!==void 0&&Number.isFinite(t)?t:Oa(e.lineHeight,e.fontSize)}function to(e){return{textDecoration:Da(e.textDecoration),textDecorationStyle:e.textDecorationStyle??`SOLID`,textDecorationThickness:e.textDecorationThickness?.value??null,textDecorationFills:ra(e.textDecorationFillPaints),textDecorationSkipInk:e.textDecorationSkipInk??!0,textUnderlineOffset:e.textUnderlineOffset?.value??null}}function no(e,t){return{text:e.textData?.characters??``,fontSize:e.fontSize??14,fontFamily:e.fontName?.family??`Inter`,fontWeight:Mr(e.fontName?.style??``),italic:e.fontName?.style.toLowerCase().includes(`italic`)??!1,textAlignHorizontal:e.textAlignHorizontal??`LEFT`,textAlignVertical:e.textAlignVertical??`TOP`,textAutoResize:e.textAutoResize??`NONE`,textCase:e.textCase??`ORIGINAL`,...to(e),leadingTrim:e.leadingTrim??`NONE`,lineHeight:eo(e),letterSpacing:ka(e.letterSpacing,e.fontSize),maxLines:e.maxLines??null,styleRuns:Pa(e),fontVariations:Gi(e),fontFeatures:Ui(e),textTruncation:e.textTruncation===`ENDING`?`ENDING`:`DISABLED`,textDirection:Ta(e,`textDirection`)||`AUTO`,derivedLayout:e.derivedTextData?.layoutSize?{width:e.derivedTextData.layoutSize.x,height:e.derivedTextData.layoutSize.y}:null,derivedTextGlyphs:Ri(e.derivedTextData,t)}}function ro(e){let t=e.stackPadding??0;return{paddingTop:e.stackVerticalPadding??t,paddingBottom:e.stackPaddingBottom??t,paddingLeft:e.stackHorizontalPadding??t,paddingRight:e.stackPaddingRight??t}}function io(e,t,n,r){let i=n===`HUG`||r===`HUG`,a=(e.fillPaints?.some(e=>e.visible!==!1)??!1)||(e.strokePaints?.some(e=>e.visible!==!1)??!1);if(!(t===`NONE`||!i||!a))return{x:e.transform?.m02??0,y:e.transform?.m12??0,width:e.size?.x??100,height:e.size?.y??100}}function ao(e,t){let n=e?.value?.[t];return typeof n==`number`&&Number.isFinite(n)&&n>0?n:null}function oo(e,t){let n=e?.value?.[t];return typeof n==`number`&&Number.isFinite(n)&&n>=0?n:null}function so(e){let t=Ga(e.stackMode),n=Ka(e.stackPrimarySizing),r=Ka(e.stackCounterSizing),i=io(e,t,n,r);return{layoutMode:t,itemSpacing:e.stackSpacing??0,...ro(e),primaryAxisSizing:n,counterAxisSizing:r,primaryAxisAlign:qa(e.stackPrimaryAlignItems??e.stackJustify),counterAxisAlign:Ja(e.stackCounterAlignItems??e.stackCounterAlign),layoutWrap:e.stackWrap===`WRAP`?`WRAP`:`NO_WRAP`,counterAxisSpacing:e.stackCounterSpacing??0,layoutPositioning:e.stackPositioning===`ABSOLUTE`?`ABSOLUTE`:`AUTO`,layoutGrow:e.stackChildPrimaryGrow??0,layoutAlignSelf:Ya(e.stackChildAlignSelf),counterAxisAlignContent:e.stackCounterAlignContent===`SPACE_BETWEEN`?`SPACE_BETWEEN`:`AUTO`,itemReverseZIndex:e.stackReverseZIndex??!1,strokesIncludedInLayout:e.strokesIncludedInLayout??!1,layoutDirection:Ta(e,`layoutDirection`)||`AUTO`,...i?{derivedLayout:i}:{}}}function co(e){return e.strokeCap??`NONE`}function lo(e,t){return e.strokeJoin??t?.vertices.find(e=>e.strokeJoin)?.strokeJoin??`MITER`}function uo(e){if(!e||typeof e!=`object`||!(`guid`in e))return null;let t=e.guid;return!t||typeof t!=`object`?null:J(t)}function fo(e){return e===`FILL`||e===`TEXT`||e===`EFFECT`||e===`GRID`?e:null}function po(e){return Array.isArray(e)?structuredClone(e):[]}function mo(e,t){if(e.type!==`TEXT_PATH`)return null;let n=e.vectorData,r=n?.vectorNetworkBlob,i=n?.normalizedSize;if(typeof r!=`number`||!i||i.x<=0||i.y<=0)return null;let a=t[r],o=e.textPathStart;try{return{network:Fa(a,n.styleOverrideTable),normalizedSize:{x:i.x,y:i.y},tValue:o?.tValue??0,forward:o?.forward??!0}}catch{return null}}function ho(e,t){let n=La(e,t),r=co(e),i=lo(e,n);return{vectorNetwork:n,fillGeometry:Ia(Ba(e.fillGeometry,t,za(e)),n),strokeGeometry:Ba(e.strokeGeometry,t),arcData:Za(e.arcData),strokeCap:r,strokeJoin:i,dashPattern:e.dashPattern??[],borderTopWeight:e.borderTopWeight??0,borderRightWeight:e.borderRightWeight??0,borderBottomWeight:e.borderBottomWeight??0,borderLeftWeight:e.borderLeftWeight??0,independentStrokeWeights:e.borderStrokeWeightsIndependent??!1,strokeMiterLimit:e.miterLimit??4}}function go(e){let t=Ua(e.type);return t===`FRAME`&&No(e)||Ta(e,`nodeType`)===`COMPONENT_SET`?`COMPONENT_SET`:t===`FRAME`&&e.resizeToFit===!0&&(e.stackMode===void 0||e.stackMode===`NONE`)?`GROUP`:t}function _o(e,t){return Math.abs((e??0)-(t??0))<=.5}function vo(e,t){if(e.type!==`TEXT`||e.textAutoResize!==`NONE`||t?.stackMode!==`HORIZONTAL`&&t?.stackMode!==`VERTICAL`||!e.textData?.characters)return!1;let n=e.derivedTextData?.layoutSize;return!n||!e.size?!1:_o(n.x,e.size.x)&&_o(n.y,e.size.y)}function yo(e,t){let n=go(e),r=ho(e,t),i=mo(e,t),a={nodeType:n,name:e.name??n,source:Fo(e,t),...Qa(e),opacity:e.opacity??1,visible:e.visible??!0,locked:e.locked??!1,blendMode:e.blendMode??`PASS_THROUGH`,booleanOperation:Wa(e),fills:ra(e.fillPaints),strokes:ia(e.strokePaints,e.strokeWeight,e.strokeAlign,r.strokeCap,r.strokeJoin,e.dashPattern??[]),effects:aa(e.effects),layoutGrids:po(e.layoutGrids),guides:Qt(e.guides),fillStyleId:uo(e.styleIdForFill),strokeStyleId:uo(e.styleIdForStrokeFill),textStyleId:uo(e.styleIdForText),effectStyleId:uo(e.styleIdForEffect),gridStyleId:uo(e.styleIdForGrid),sharedStyleType:fo(e.styleType),...$a(e),...no(e,t),horizontalConstraint:Xa(e.horizontalConstraint),verticalConstraint:Xa(e.verticalConstraint),...so(e),...r,minWidth:ao(e.minSize,`x`),maxWidth:oo(e.maxSize,`x`),minHeight:ao(e.minSize,`y`),maxHeight:oo(e.maxSize,`y`),isMask:e.mask??!1,maskType:e.maskType??`ALPHA`,maskIsOutline:e.maskIsOutline??!1,expanded:!0,autoRename:e.autoRename??!0,boundVariables:_a(e),variableModes:Va(e),exportSettings:Sa(e),pluginData:Ca(e),librarySource:wa(e),pluginRelaunchData:Ea(e),clipsContent:e.frameMaskDisabled===!1&&e.resizeToFit!==!0,componentId:Vo(e),componentPropertyDefinitions:So(e),componentPropertyReferences:Co(e),componentPropertyAssignments:To(e),componentPropertyValues:Do(e),...Mo(e)};la(a,i);let o=ha(e);return o&&a.textPathBox&&(a.textPathBox={x:o.x+a.textPathBox.x,y:o.y+a.textPathBox.y,width:o.width,height:o.height}),a}let bo={VARIANT:`VARIANT`,TEXT:`TEXT`,BOOL:`BOOLEAN`,BOOLEAN:`BOOLEAN`,INSTANCE_SWAP:`INSTANCE_SWAP`};function xo(e){if(!e||typeof e!=`object`)return``;let t=e;return typeof t.boolValue==`boolean`?String(t.boolValue):typeof t.textValue==`string`?t.textValue:t.textValue&&typeof t.textValue==`object`?t.textValue.characters??``:t.guidValue?J(t.guidValue):``}function So(e){let t=e.componentPropDefs;if(!t?.length)return[];let n=[];for(let e of t){if(!e.id||!e.name)continue;let t=bo[e.type??``]??`VARIANT`;n.push({id:J(e.id),name:e.name,type:t,defaultValue:xo(e.initialValue),variantOptions:t===`VARIANT`?e.preferredValues?.stringValues:void 0,preferredValues:t===`INSTANCE_SWAP`?e.preferredValues?.instanceSwapValues?.map(e=>e.key).filter(e=>e!==void 0):void 0})}return n}function Co(e){let t=e.componentPropRefs;if(!t?.length)return[];let n={0:`VISIBLE`,1:`TEXT`,2:`INSTANCE_SWAP`,VISIBLE:`VISIBLE`,TEXT_DATA:`TEXT`,OVERRIDDEN_SYMBOL_ID:`INSTANCE_SWAP`};return t.flatMap(e=>{let t=n[String(e.componentPropNodeField)];return e.defID&&t&&!e.isDeleted?[{propertyId:J(e.defID),field:t}]:[]})}function wo(e){if(e.value&&(e.value.boolValue!==void 0||e.value.textValue!==void 0||e.value.guidValue!==void 0))return xo(e.value);let t=e.varValue?.value;return t?.symbolIdValue?.guid?J(t.symbolIdValue.guid):t?.boolValue===void 0?t?.textValue===void 0?t?.textDataValue?.characters??``:t.textValue:String(t.boolValue)}function To(e){let t=e.componentPropAssignments;return t?.length?Object.fromEntries(t.flatMap(e=>e.defID?[[J(e.defID),wo(e)]]:[])):{}}function Eo(e){let t=e.variantPropSpecs;return t?.length?t.filter(e=>!!e.propDefId).map(e=>({propDefId:J(e.propDefId),value:e.value??``})):[]}function Do(e){let t=Eo(e),n=new Map(So(e).map(e=>[e.id,e.name]));if(t.length>0&&n.size>0){let e={};for(let r of t)e[n.get(r.propDefId)??r.propDefId]=r.value;return e}let r=e.name;return r?.includes(`=`)?Li(r):{}}function Oo(e){if(!e||typeof e!=`object`)return null;let t=e;return typeof t.sessionID!=`number`||typeof t.localID!=`number`?null:J({sessionID:t.sessionID,localID:t.localID})}function ko(e){return typeof e==`string`?e:null}function Ao(e){return typeof e==`string`?e:``}function jo(e){return typeof e==`boolean`&&e}function Mo(e){let t=e.symbolLinks??[];return{componentKey:ko(e.componentKey),sourceLibraryKey:ko(e.sourceLibraryKey),publishId:Oo(e.publishID),overrideKey:Oo(e.overrideKey),sharedSymbolVersion:ko(e.sharedSymbolVersion),publishedVersion:ko(e.publishedVersion),isPublishable:jo(e.isPublishable),isSymbolPublishable:jo(e.isSymbolPublishable),symbolDescription:Ao(e.symbolDescription),symbolLinks:t.filter(e=>typeof e.uri==`string`).map(e=>({uri:e.uri,displayName:e.displayName,displayText:e.displayText})),variantPropSpecs:Eo(e)}}function No(e){let t=e.componentPropDefs;return t?.length?t.some(e=>e.type===`VARIANT`):!1}function Po(e){return{stackMode:e.stackMode,stackSpacing:e.stackSpacing,stackPadding:e.stackPadding,stackPaddingRight:e.stackPaddingRight,stackPaddingBottom:e.stackPaddingBottom,stackCounterAlign:e.stackCounterAlign,stackJustify:e.stackJustify,stackCounterAlignItems:e.stackCounterAlignItems,stackPrimaryAlignItems:e.stackPrimaryAlignItems,stackPrimarySizing:e.stackPrimarySizing,stackCounterSizing:e.stackCounterSizing,stackVerticalPadding:e.stackVerticalPadding,stackHorizontalPadding:e.stackHorizontalPadding,stackWrap:e.stackWrap,stackPositioning:e.stackPositioning,stackChildPrimaryGrow:e.stackChildPrimaryGrow,stackChildAlignSelf:e.stackChildAlignSelf,stackCounterSpacing:e.stackCounterSpacing,bordersTakeSpace:e.bordersTakeSpace,stackReverseZIndex:e.stackReverseZIndex}}function Fo(e,t){return{format:`fig`,id:e.guid?J(e.guid):null,orderKey:e.parentIndex?.position??null,editedFields:[],fig:{...zo(e,t),...Bo(e,t),layout:Po(e)}}}function Io(e,t,n){let r=t.stackMode,i=r===`HORIZONTAL`,a=r===`VERTICAL`;e.sort((e,t)=>{let r=n.get(e)?.parentIndex?.position??``,o=n.get(t)?.parentIndex?.position??``;if(r<o)return-1;if(r>o)return 1;if(i||a){let r=i?`m02`:`m12`,a=n.get(e)?.transform?.[r]??0,o=n.get(t)?.transform?.[r]??0;if(a!==o)return a-o}return 0})}function Lo(e,t){if(e instanceof Uint8Array)return e;if(Array.isArray(e))return e.map(e=>Lo(e,t));if(!e||typeof e!=`object`)return e;let n={};for(let[r,i]of Object.entries(e))if((r===`commandsBlob`||r===`vectorNetworkBlob`)&&typeof i==`number`){let e=t[i];e==null?n[r]=i:n[r]={__openPencilFigmaBlob:e instanceof Uint8Array?e:new Uint8Array(Object.values(e))}}else n[r]=Lo(i,t);return n}let Ro=`styleIdForFill.styleIdForStrokeFill.styleIdForText.styleIdForEffect.styleIdForGrid.styleType.componentPropAssignments.backgroundPaints.layoutGrids.exportSettings.componentPropDefs.componentPropRefs.variantPropSpecs.stateGroupPropertyValueOrders.isStateGroup.version.sourceLibraryKey.userFacingVersion.description.key.sortPosition.detachedSymbolId.documentColorProfile.variableConsumptionMap.variableModeBySetMap.parameterConsumptionMap.editInfo.backgroundColor.blendMode.pageType.isPageDivider.guides.handoffStatusMap.annotationCategories.miterLimit.mask.maskType.maskIsOutline.strokeWeight.strokeJoin.borderStrokeWeightsIndependent.borderTopWeight.borderRightWeight.borderBottomWeight.borderLeftWeight.minSize.maxSize.targetAspectRatio.gridRows.gridColumns.gridRowAnchor.gridColumnAnchor.gridColumnsSizing.gridRowsSizing.gridChildVerticalAlign.gridChildHorizontalAlign.textAutoResize.textAlignHorizontal.textAlignVertical.textData.lineHeight.fontName.fontSize.letterSpacing.textTracking.fontVersion.textUserLayoutVersion.textExplicitLayoutVersion.fontVariations.fontVariantCommonLigatures.fontVariantContextualLigatures.toggledOnOTFeatures.toggledOffOTFeatures.leadingTrim.textDecorationFillPaints.textUnderlineOffset.textDecorationThickness.textDecorationStyle.semanticWeight.semanticItalic.maxLines.textPathStart.derivedTextData.fillPaints.strokePaints.effects.sectionStatusInfo.prototypeStartNodeID.prototypeInteractions.transitionInfo.codeSyntax.lockMode.slideThemeMap.isSoftDeleted.brushType.scatterStrokeSettings.vectorOperationVersion.vectorData.fillGeometry.strokeGeometry`.split(`.`);function zo(e,t){let n={};for(let r of Ro){let i=e[r];i!==void 0&&(n[r]=Lo(i,t))}return{rawSize:e.size?{...e.size}:null,rawTransform:e.transform?{...e.transform}:null,rawNodeFields:n}}function Bo(e,t){let n=e.symbolData;return{symbolOverrides:Lo(n?.symbolOverrides??[],t),componentPropAssignments:Lo(e.componentPropAssignments??[],t),derivedSymbolData:Lo(e.derivedSymbolData??[],t),derivedSymbolDataLayoutVersion:typeof e.derivedSymbolDataLayoutVersion==`number`?e.derivedSymbolDataLayoutVersion:null,uniformScaleFactor:typeof n?.uniformScaleFactor==`number`?n.uniformScaleFactor:null}}function Vo(e){let t=e.symbolData;return t?.symbolID?J(t.symbolID):``}function Ho(e,t,n,r){let i=e.getNode(t),a=e.getNode(n);if(!i||!a)return;let o=e.getNode(r)?.instanceOverrides,s=i.childIds.map(t=>e.getNode(t)).filter(e=>e!==void 0),c=a.childIds.map(t=>e.getNode(t)).filter(e=>e!==void 0),l=new Set,u=new Set,d=(t,n)=>{n.type===`INSTANCE`?o&&rn(o,r,n.id,`sourceComponentId`,t.id):n.componentId||=t.id,l.add(t.id),u.add(n.id),n.type!==`INSTANCE`&&t.childIds.length>0&&n.childIds.length>0&&Ho(e,t.id,n.id,r)};for(let e of c){if(!e.overrideKey||u.has(e.id))continue;let t=s.find(t=>!l.has(t.id)&&t.overrideKey===e.overrideKey&&t.type===e.type);t&&d(t,e)}let f=s.filter(e=>!l.has(e.id)),p=c.filter(e=>!u.has(e.id));if(f.length===p.length&&f.every((e,t)=>e.type===p[t]?.type))for(let e=0;e<f.length;e++){let t=f[e],n=p[e];d(t,n)}}function Uo(e,t){e.preserveSourceMetadataDuring(()=>{for(let n of e.getAllNodes()){if(n.type!==`INSTANCE`||!n.componentId||t&&!t.has(n.id))continue;let r=e.getNode(n.componentId);r&&Ho(e,r.id,n.id,n.id)}})}let Wo=[`fontSize`,`fontName`,`lineHeight`,`letterSpacing`,`textDecoration`,`textCase`];function Go(e,t,n){if(t?.guid)return e.get(J(t.guid));if(!t?.assetRef||!n)return;let{key:r,version:i}=t.assetRef,a=(i?n.get(`${r}@${i}`):void 0)??n.get(r);return a?e.get(a):void 0}function Ko(e,t,n){let r=Go(e,t.styleIdForFill,n);r?.styleType===`FILL`&&r.fillPaints&&(t.fillPaints=r.fillPaints);let i=Go(e,t.styleIdForStrokeFill,n);i?.styleType===`FILL`&&i.fillPaints&&(t.strokePaints=i.fillPaints)}function qo(e,t,n){let r=Go(e,t.styleIdForEffect,n);r?.styleType===`EFFECT`&&r.effects&&(t.effects=r.effects);let i=Go(e,t.styleIdForGrid,n);i?.styleType===`GRID`&&i.layoutGrids&&(t.layoutGrids=i.layoutGrids)}function Jo(e,t,n){let r=Go(e,t.styleIdForText,n);if(!(r?.type!==`TEXT`||r.styleType!==`TEXT`))for(let e of Wo)e===`textDecoration`?t.textDecoration=r.textDecoration:r[e]!==void 0&&Object.assign(t,{[e]:r[e]})}function Yo(e,t,n){Ko(e,t,n),qo(e,t,n),Jo(e,t,n)}function Xo(e,t,n){let r={},i=Ba(e.fillGeometry,n,za(e)),a=Ba(e.strokeGeometry,n);if(i.length>0?r.fillGeometry=Ia(i,t.vectorNetwork):e.size&&t.fillGeometry.length>0&&t.width>0&&t.height>0&&(r.fillGeometry=jn(t.fillGeometry,e.size.x/t.width,e.size.y/t.height)),a.length>0?r.strokeGeometry=a:e.size&&t.strokeGeometry.length>0&&t.width>0&&t.height>0&&(r.strokeGeometry=jn(t.strokeGeometry,e.size.x/t.width,e.size.y/t.height)),e.size&&t.vectorNetwork?.vertices.length){let n=hn(t.vectorNetwork),i=Math.min(...n.vertices.map(({x:e})=>e)),a=Math.min(...n.vertices.map(({y:e})=>e)),o=n.vertices.map(({x:e})=>e),s=n.vertices.map(({y:e})=>e),c=Math.max(...o)-Math.min(...o),l=Math.max(...s)-Math.min(...s),u=c===0?1:e.size.x/c,d=l===0?1:e.size.y/l;for(let e of n.vertices)e.x=i+(e.x-i)*u,e.y=a+(e.y-a)*d;for(let e of n.segments)e.tangentStart.x*=u,e.tangentStart.y*=d,e.tangentEnd.x*=u,e.tangentEnd.y*=d;r.vectorNetwork=n}return r}function Zo(e,t,n){let r=t.get(n);if(r!==void 0)return r;let i=e.graph.getChildren(n).filter(e=>e.visible).length;return t.set(n,i),i}function Qo(e,t,n){if(!n.parentId||Zo(e,t,n.parentId)!==1||!n.componentId)return null;let r=e.graph.getNode(n.componentId),i=e.graph.getNode(n.parentId);return!r||!i?null:r.x>=0&&r.y>=0&&r.x+r.width<=i.width+.01&&r.y+r.height<=i.height+.01?{x:r.x,y:r.y}:null}function $o(e,t,n){if(e.rotation===0&&!e.flipX&&!e.flipY)return null;let r=Or(e),i=t/2,a=n/2;return{x:r[2]-i+r[0]*i+r[1]*a,y:r[5]-a+r[3]*i+r[4]*a}}function es(e,t,n){let r={};e.fontSize!==void 0&&(r.fontSize=e.fontSize),e.lineHeight!==void 0&&(r.lineHeight=Oa(e.lineHeight,e.fontSize)),e.letterSpacing!==void 0&&(r.letterSpacing=ka(e.letterSpacing,e.fontSize)),e.strokeWeight!==void 0&&n.strokes.length>0&&(r.strokes=n.strokes.map(t=>({...t,weight:e.strokeWeight})));let i=Ri(e.derivedTextData,t);return i.length>0&&(r.derivedTextGlyphs=i),r}function ts(e,t,n,r){let i=es(n,e.blobs,r),a={};if(n.size&&(i.width=n.size.x,i.height=n.size.y,a.width=n.size.x,a.height=n.size.y),n.transform){let e=Qa({transform:n.transform,size:n.size??{x:r.width,y:r.height}});i.x=e.x,i.y=e.y,i.rotation=e.rotation,i.flipX=e.flipX,i.flipY=e.flipY,a.x=e.x,a.y=e.y}else if(n.size){let o=$o(r,n.size.x,n.size.y)??Qo(e,t,r);o&&(i.x=o.x,i.y=o.y,a.x=o.x,a.y=o.y)}return Object.keys(a).length>0&&(i.derivedLayout=a),Object.assign(i,Xo(n,r,e.blobs)),{updates:i,hasSize:n.size!==void 0}}function*ns(e,t){if(!t){yield*e.getAllNodes();return}for(let n of t){let t=e.getNode(n);t&&(yield t)}}function rs(e,t,n={}){return{...ar(e),componentId:t,derivedLayout:e.derivedLayout?{...e.derivedLayout}:null,...n}}let is=new WeakSet,as=new WeakMap;function os(e){let t=as.get(e);return t||(t=new Map([...e].map(([e,t])=>[e,new Set(t)])),as.set(e,t)),t}function ss(e,t,n){if(is.has(t))return;let r=os(t);for(let i of ns(e,n)){if(!i.componentId)continue;let e=r.get(i.componentId);e||(e=new Set,r.set(i.componentId,e),t.set(i.componentId,[])),!e.has(i.id)&&(e.add(i.id),t.get(i.componentId)?.push(i.id))}is.add(t)}function cs(e,t,n){let r=os(n);for(let i of t){let t=e.getNode(i);if(!t?.componentId)continue;let a=r.get(t.componentId);if(a||(a=new Set,r.set(t.componentId,a)),a.has(t.id))continue;a.add(t.id);let o=n.get(t.componentId);o?o.push(t.id):n.set(t.componentId,[t.id])}}function ls(e,t,n){let r=[],i=[t],a=0;for(;a<i.length;){let t=e.getNode(i[a]);a++,t&&(r.push(t.id),i.push(...t.childIds))}cs(e,r,n)}function us(e,t){let n=[],r=e.getNode(t);if(!r)return n;let i=(t,r)=>{let a=e.getNode(t);a&&(n.push({id:a.id,path:r,type:a.type}),a.childIds.forEach((e,t)=>i(e,[...r,t])))};return r.childIds.forEach((e,t)=>i(e,[t])),n}function ds(e,t,n){let r=e.getNode(t);if(!r)return null;for(let t of n){let n=r.childIds[t];if(!n||(r=e.getNode(n),!r))return null}return r}function fs(e,t,n,r){return new Set([...r?.get(t)??[],...r?.get(n)??[],...e.instanceIndex.get(t)??[],...e.instanceIndex.get(n)??[]])}function ps(e,t,n){if(!e)return;let r=os(e),i=r.get(t);if(i||(i=new Set,r.set(t,i)),i.has(n))return;i.add(n);let a=e.get(t);a||(a=[],e.set(t,a)),a.push(n)}function ms(e,t,n,r,i){r&&ss(e,r,i);for(let i of n){let n=ds(e,t,i.path);if(!n||n.type!==i.type)continue;let a=fs(e,i.id,n.id,r);for(let t of a){let a=e.getNode(t);a?.componentId!==i.id&&a?.componentId!==n.id||(e.updateNode(t,rs(n,n.id)),ps(r,n.id,t))}}}let hs=new WeakMap,gs=new WeakMap,_s=new WeakMap,vs=new WeakMap,ys=new WeakMap,bs=new WeakMap;function xs(e){function t(n,r=0){let i=e.preComputedRoot.get(n);if(i!==void 0)return i;if(r>20)return n;let a=e.graph.getNode(n);if(a?.componentId&&a.componentId!==n){let i=t(a.componentId,r+1);return e.preComputedRoot.set(n,i),i}return e.preComputedRoot.set(n,n),n}for(let n of ns(e.graph,e.activeNodeIds)){if(!n.componentId)continue;t(n.id);let r=e.preComputedClones.get(n.componentId);r?r.push(n.id):e.preComputedClones.set(n.componentId,[n.id])}}function $(e,t,n=0){let r=e.componentIdRoot.get(t);if(r!==void 0)return r;if(n>20)return e.componentIdRoot.set(t,t),t;let i=e.graph.getNode(t);if(i?.componentId){let r=$(e,i.componentId,n+1);return e.componentIdRoot.set(t,r),r}let a=e.nodeIdToGuid.get(t);if(a){let r=e.changeMap.get(a)?.symbolData?.symbolID;if(r){let i=e.guidToNodeId.get(J(r));if(i&&i!==t){let r=$(e,i,n+1);return e.componentIdRoot.set(t,r),r}}}return e.componentIdRoot.set(t,t),t}function Ss(e){let t=new Map;for(let[n,r]of e.changeMap){let e=r.parentIndex?.guid?J(r.parentIndex.guid):null,i=r.symbolData?.symbolID?J(r.symbolData.symbolID):null;if(!e||!i)continue;let a=`${e}\0${i}`,o=t.get(a);o?o.push(n):t.set(a,[n])}for(let n of t.values())n.sort((t,n)=>{let r=e.changeMap.get(t),i=e.changeMap.get(n);return(r?.transform?.m12??0)-(i?.transform?.m12??0)||(r?.transform?.m02??0)-(i?.transform?.m02??0)});return t}function Cs(e){let t=gs.get(e);if(t)return t;let n=Ss(e);return gs.set(e,n),n}function ws(e,t){let n=hs.get(e);if(n||(n=new Map,hs.set(e,n)),n.has(t))return n.get(t)??null;let r=e.changeMap.get(t),i=r?.parentIndex?.guid?J(r.parentIndex.guid):null,a=r?.symbolData?.symbolID?J(r.symbolData.symbolID):null;if(!r||!i||!a)return n.set(t,null),null;let o=(Cs(e).get(`${i}\0${a}`)??[]).indexOf(t),s=o===-1?null:o;return n.set(t,s),s}function Ts(e,t,n){let r=ys.get(e);r||(r=new Map,ys.set(e,r));let i=`${t}\0${n}`;if(r.has(i))return r.get(i)??null;let a=(t,r)=>{let i=e.graph.getNode(t);if(!i)return null;if(t===n||i.componentId===n)return r;for(let e=0;e<i.childIds.length;e++){let t=a(i.childIds[e],[...r,e]);if(t)return t}return null},o=a(t,[]);return r.set(i,o),o}function Es(e,t,n){let r=e.graph.getNode(t);if(!r?.componentId)return null;let i=Ts(e,r.componentId,n);if(!i)return null;let a=r;for(let t of i){let n=a.childIds[t];if(!n)return null;let r=e.graph.getNode(n);if(!r)return null;a=r}return a.id}function Ds(e,t,n,r){if(!n||!r)return null;let i=null,a=0,o=t=>{if(a>1)return;let s=e.graph.getNode(t);if(s){s.name===n&&s.type===r&&(a++,i=t);for(let e of s.childIds)o(e)}};return o(t),a===1?i:null}function Os(e,t,n,r){let i=ws(e,r);if(i==null)return null;let a=e.preComputedRoot.get(n)??$(e,n),o=_s.get(e);o||(o=new Map,_s.set(e,o));let s=`${t}\0${a}`,c=o.get(s);if(!c){c=[];let n=t=>{let r=e.graph.getNode(t);if(r){r.componentId&&(e.preComputedRoot.get(r.componentId)??$(e,r.componentId))===a&&c?.push(t);for(let e of r.childIds)n(e)}};n(t),c.sort((t,n)=>{let r=e.graph.getNode(t),i=e.graph.getNode(n);return(r?.y??0)-(i?.y??0)||(r?.x??0)-(i?.x??0)}),o.set(s,c)}return c[i]??null}function ks(e,t,n){let r=vs.get(e);r||(r=new Map,vs.set(e,r));let i=`${t}\0${n}`;if(r.has(i))return r.get(i)??null;let a=e.graph.getNode(t);if(!a)return null;for(let t of a.childIds)if(e.graph.getNode(t)?.componentId===n)return r.set(i,t),t;let o=e.preComputedRoot.get(n)??$(e,n);if(o){let t=null,n=!1;for(let r of a.childIds){let i=e.graph.getNode(r);if(i?.componentId&&(e.preComputedRoot.get(i.componentId)??$(e,i.componentId))===o){if(t){n=!0;break}t=r}}if(t&&!n)return r.set(i,t),t}for(let t of a.childIds){let a=ks(e,t,n);if(a)return r.set(i,a),a}return null}function As(e,t,n,r,i){return r?e.graph.getNode(t)?.componentId===r?t:Es(e,t,r)??ks(e,t,r)??Os(e,t,r,n)??Ds(e,t,i?.name,i?.type):Ds(e,t,i?.name,i?.type)}function js(e,t,n){let r=bs.get(e);r||(r=new Map,bs.set(e,r));let i=t,a=[];for(let o=0;o<n.length;o++){let s=J(n[o]);a.push(s);let c=`${t}\0${a.join(`/`)}`,l=r.get(c);if(l&&e.graph.getNode(l)){i=l;continue}l&&r.delete(c);let u=e.overrideKeyToGuid.get(s)??s,d=e.changeMap.get(u),f=d?.symbolData?.symbolID?J(d.symbolData.symbolID):null,p=e.guidToNodeId.get(u)??(f?e.guidToNodeId.get(f):void 0),m=As(e,i,u,p,d);if(m){i=m,r.set(c,m);continue}let h=e.graph.getNode(i);if(h?.childIds.length===1){i=h.childIds[0],o--,a.pop();continue}return null}return i}function Ms(e,t){let n=[],r=t=>{let i=e.graph.getNode(t);if(i){i.strokes.length>0&&n.push(Cn(i.strokes));for(let e of i.childIds)r(e)}};return r(t),n}function Ns(e,t,n){let r=0,i=t=>{let a=e.graph.getNode(t);if(a){a.strokes.length>0&&(r<n.length&&e.graph.preserveSourceMetadataDuring(()=>{e.graph.updateNode(t,{strokes:Cn(n[r])})}),r++);for(let e of a.childIds)i(e)}};i(t)}function Ps(e,t){if(!t)return``;let n=t.parentId?e.graph.getNode(t.parentId):void 0;return n?.type===`COMPONENT_SET`?n.name:t.name}function Fs(e,t){let n=ar(t);n.width=e.width,n.height=e.height,n.boundVariables={...n.boundVariables};for(let t of[`width`,`height`]){let r=e.boundVariables[t];r&&(n.boundVariables[t]=r)}return n}function Is(e,t,n){let r=e.graph.getNode(t);if(r?.type!==`INSTANCE`)return;let i=Ms(e,t),a=us(e.graph,t),o=r.componentId?$(e,r.componentId):void 0,s=o?e.graph.getNode(o):void 0;for(let t of Array.from(r.childIds))e.graph.deleteNode(t);let c=e.graph.getNode(n),l=c?{...Fs(r,c),componentId:n}:{componentId:n},u=Ps(e,s),d=Ps(e,c);d&&u&&(r.name===u||r.name===s?.name)&&(l.name=d),e.graph.preserveSourceMetadataDuring(()=>e.graph.updateNode(t,l)),c&&c.childIds.length>0&&(e.graph.populateInstanceChildren(t,n,`fig-import`),ls(e.graph,t,e.preComputedClones),Ns(e,t,i)),ms(e.graph,t,a,e.preComputedClones,e.activeNodeIds),e.swappedInstances.add(t),e.componentIdRoot.clear(),_s.delete(e),vs.delete(e)}let Ls={text:`text`,visible:`visible`,opacity:`opacity`,fills:`fills`,strokes:`strokes`,effects:`effects`,styleRuns:`styleRuns`,layoutGrow:`layoutGrow`,textAutoResize:`textAutoResize`,locked:`locked`,x:`x`,y:`y`,width:`width`,height:`height`,derivedLayout:`derivedLayout`,fontSize:`fontSize`,lineHeight:`lineHeight`,letterSpacing:`letterSpacing`,fillGeometry:`fillGeometry`,strokeGeometry:`strokeGeometry`};function Rs(e,t,n){let r=e.get(t);r?r.add(n):e.set(t,new Set([n]))}function zs(e,t,n){for(let r of Object.keys(n)){let n=Ls[r];n&&Rs(e,t,n)}}function Bs(e,t,n){return e?.get(t)?.has(n)===!0}function Vs(e,t){t.strokes&&=t.strokes.map((t,n)=>{if(n>=e.strokes.length)return{...t,cap:e.strokeCap,join:e.strokeJoin,dashPattern:e.dashPattern};let r=e.strokes[n];return{...t,cap:r.cap,join:r.join,dashPattern:r.dashPattern}})}function Hs(e,t){let n=!1;if(t.swapComponentId&&(Is(e,t.targetId,t.swapComponentId),Rs(e.protectedFields,t.targetId,`structure`),n=!0),t.props&&Object.keys(t.props).length>0){let r=e.graph.getNode(t.targetId);if(r){let i=t.props;i.boundVariables&&={...r.boundVariables,...i.boundVariables},Vs(r,i),e.graph.preserveSourceMetadataDuring(()=>e.graph.updateNode(t.targetId,i)),zs(e.protectedFields,t.targetId,i),n=!0}}return n}function Us(e,t,n){return!Bs(e,t,n)}function Ws(e,t,n){switch(e){case`text`:n.text=t.text;break;case`visible`:n.visible=t.visible;break;case`opacity`:n.opacity=t.opacity;break;case`locked`:n.locked=t.locked;break;case`layoutGrow`:n.layoutGrow=t.layoutGrow;break;case`textAutoResize`:n.textAutoResize=t.textAutoResize;break}}function Gs(e,t){return(n,r,i,a)=>{n[e]!==r[e]&&Us(a,r.id,t)&&Ws(e,n,i)}}let Ks=[Gs(`text`,`text`),Gs(`visible`,`visible`),Gs(`opacity`,`opacity`),Gs(`locked`,`locked`),Gs(`layoutGrow`,`layoutGrow`),Gs(`textAutoResize`,`textAutoResize`)];function qs(e,t,n){switch(e){case`fills`:n.fills=Y(t.fills,X(t.fills));break;case`strokes`:n.strokes=Y(t.strokes,Cn(t.strokes));break;case`effects`:n.effects=Y(t.effects,wn(t.effects));break;case`styleRuns`:n.styleRuns=Y(t.styleRuns,En(t.styleRuns));break}}function Js(e,t,n,r){let i=`${e}/`,a=r.boundVariables??n.boundVariables,o=Ir(a,Object.keys(a).filter(e=>e.startsWith(i)));for(let[e,n]of Object.entries(t.boundVariables))e.startsWith(i)&&(o[e]=n);r.boundVariables=o}function Ys(e,t){return(n,r,i,a)=>{!Sn(n[e],r[e])&&Us(a,r.id,t)&&(qs(e,n,i),(e===`fills`||e===`strokes`)&&Js(e,n,r,i))}}let Xs=[Ys(`fills`,`fills`),Ys(`strokes`,`strokes`),Ys(`effects`,`effects`),Ys(`styleRuns`,`styleRuns`)];function Zs(e,t,n,r){let i=t.boundVariables[e];if(n.boundVariables[e]===i)return;let a={...r.boundVariables??n.boundVariables};i&&(a[e]=i),r.boundVariables=i?a:Ir(a,[e])}function Qs(e,t,n,r){for(let i of Ks)i(e,t,n,r);for(let i of Xs)i(e,t,n,r);Zs(`opacity`,e,t,n)}function $s(e,t,n,r){let i={};Qs(t,n,i,r),Object.keys(i).length>0&&e.updateNode(n.id,i)}function ec(e,t,n,r,i,a,o){let s=e.getNode(t);if(!s)return;let c=a??nc(e,o),l=us(e,n.id);for(let t of Array.from(n.childIds))e.deleteNode(t);e.updateNode(n.id,rs(s,s.componentId,{name:s.name})),$s(e,s,n,i),s.childIds.length>0&&(e.populateInstanceChildren(n.id,t,`fig-import`),ls(e,n.id,c)),ms(e,n.id,l,c,o),r.add(n.id)}function tc(e,t,n,r,i,a,o,s){let c=e.getNode(t),l=e.getNode(n);if(!c||!l)return;let u=o??nc(e,s),d=Math.min(c.childIds.length,l.childIds.length);for(let t=0;t<d;t++){if(i?.has(l.childIds[t]))continue;let n=e.getNode(c.childIds[t]),o=e.getNode(l.childIds[t]);if(!(!n||!o||n.type!==o.type)&&!Wn(e,o)){if(n.type===`INSTANCE`&&n.componentId!==o.componentId){ec(e,c.childIds[t],o,r,a,u,s);continue}$s(e,n,o,a),tc(e,c.childIds[t],l.childIds[t],r,i,a,u,s)}}}function nc(e,t){let n=new Map;for(let r of ns(e,t)){if(!r.componentId)continue;let e=n.get(r.componentId);e||(e=[],n.set(r.componentId,e)),e.push(r.id)}return n}function rc(e,t){let n=new Set(t);for(let r of t){let t=e.getNode(r);for(;t?.parentId;){let r=e.getNode(t.parentId);if(!r)break;(r.type===`INSTANCE`||r.type===`COMPONENT`)&&n.add(r.id),t=r}}return n}function ic(e,t){let n=new Set,r=[...e];for(let e=r.pop();e!==void 0;e=r.pop()){let i=t.get(e);if(i)for(let e of i)n.has(e)||(n.add(e),r.push(e))}return n}function ac(e,t){if(!t)return e;let n=new Map;for(let r of[t,e])for(let[e,t]of r){let r=n.get(e);if(r)for(let e of t)r.includes(e)||r.push(e);else n.set(e,[...t])}return n}function oc(e,t,n,r,i){if(t.size===0)return;let a=ac(nc(e,n),i),o=new Set(t),s=[...t].map(e=>({lineageId:e,sourceId:e})),c=0;for(;c<s.length;){let{lineageId:t,sourceId:n}=s[c];c++;let i=e.getNode(n);if(i)for(let c of a.get(t)??[]){if(o.has(c))continue;o.add(c);let t=e.getNode(c);t&&$s(e,i,t,r),s.push({lineageId:c,sourceId:t?.id??n})}}}function sc(e,t,n,r,i,a,o){if(t.size===0)return;r.clear();let s=nc(e,a),c=rc(e,t),l=ic(c,s),u=i&&i.size>0?new Set([...t,...i]):t,d=new Set,f=[...c],p=0;for(;p<f.length;){let t=f[p];p++;let r=s.get(t);if(!r)continue;let i=e.getNode(t);if(i)for(let c of r){if(!l.has(c)||d.has(c))continue;d.add(c);let r=e.getNode(c);if(r&&!Wn(e,r)){if(u.has(c)){i.type===`TEXT`&&r.type===`TEXT`&&!Bs(o,r.id,`text`)&&e.updateNode(r.id,{text:i.text}),f.push(c);continue}if($s(e,i,r,o),i.childIds.length!==r.childIds.length){let n=us(e,r.id);for(let t of Array.from(r.childIds))e.deleteNode(t);i.childIds.length>0&&(e.populateInstanceChildren(r.id,t,`fig-import`),ls(e,r.id,s)),ms(e,r.id,n,s,a)}else i.childIds.length>0&&r.childIds.length>0&&tc(e,t,r.id,n,u,o,s,a);f.push(c)}}}}function cc(e,t){if(t.type!==`INSTANCE`||!e.derivedLayout)return{};let n=e.derivedLayout,r={derivedLayout:{...n,...t.derivedLayout,x:n.x??t.derivedLayout?.x,y:n.y??t.derivedLayout?.y}};return n.x!==void 0&&(r.x=n.x),n.y!==void 0&&(r.y=n.y),r}function lc(e,t,n,r,i){let a={};return i.has(r)?cc(t,n):(t.width!==n.width&&(a.width=t.width),t.height!==n.height&&(a.height=t.height),t.x!==n.x&&(a.x=t.x),t.y!==n.y&&(a.y=t.y),e.geometryOverrideNodes.has(r)||(t.fillGeometry!==n.fillGeometry&&(a.fillGeometry=On(t.fillGeometry)),t.strokeGeometry!==n.strokeGeometry&&(a.strokeGeometry=On(t.strokeGeometry))),t.text===n.text&&t.derivedTextGlyphs&&(a.derivedTextGlyphs=structuredClone(t.derivedTextGlyphs)),t.text===n.text&&t.derivedLayout&&(a.derivedLayout={...t.derivedLayout}),a)}function uc(e,t){dc(e,t),mc(e)}function dc(e,t){for(let n of t){let t=e.graph.getNode(n);if(t?.layoutMode!==`NONE`||t.childIds.length!==1)continue;let r=e.graph.getNode(t.childIds[0]);if(!r||r.childIds.length>0||r.horizontalConstraint!==`SCALE`||r.verticalConstraint!==`SCALE`)continue;let i=r.derivedLayout?.width,a=r.derivedLayout?.height,o=i!==void 0&&a!==void 0,s=!o&&r.type===`ROUNDED_RECTANGLE`&&r.fills.some(e=>e.type===`IMAGE`);if(!o&&!s)continue;let c=i??t.width,l=a??t.height;r.width===c&&r.height===l||e.graph.updateNode(r.id,{width:c,height:l})}}function fc(e,t){return e.counterAxisAlign===`CENTER`?e.layoutMode===`HORIZONTAL`?t.height<=1&&t.width>t.height:e.layoutMode===`VERTICAL`&&t.width<=1&&t.height>t.width:!1}function pc(e,t){if(t.source.format!==null||!t.componentId||!t.name.endsWith(`Divider`)||!t.parentId||t.derivedLayout?.x!==void 0||t.derivedLayout?.y!==void 0)return null;let n=e.getNode(t.parentId),r=e.getNode(t.componentId),i=r?.derivedLayout;return!n||!r||i?.x===void 0||i.y===void 0||t.width!==r.width||t.height!==r.height||!fc(n,t)?null:n.layoutMode===`HORIZONTAL`?{axis:`y`,position:i.y}:{axis:`x`,position:i.x}}function mc(e){for(let t of ns(e.graph,e.activeNodeIds)){let n=pc(e.graph,t);n&&e.graph.updateNode(t.id,{derivedLayout:{...t.derivedLayout,[n.axis]:n.position}})}}function hc(e){for(let t of ns(e.graph,e.activeNodeIds)){if(t.source.format===`fig`||!t.derivedLayout||!t.parentId||t.layoutPositioning===`ABSOLUTE`)continue;let n=e.graph.getNode(t.parentId);if(!n||n.source.format===`fig`||n.layoutMode!==`NONE`||!n.derivedLayout)continue;let r={};t.horizontalConstraint===`STRETCH`&&t.derivedLayout.width!==void 0&&t.derivedLayout.width===n.derivedLayout.width&&(r.width=t.derivedLayout.width),t.verticalConstraint===`STRETCH`&&t.derivedLayout.height!==void 0&&t.derivedLayout.height===n.derivedLayout.height&&(r.height=t.derivedLayout.height),Object.keys(r).length>0&&e.graph.updateNode(t.id,r)}}function gc(e,t,n){if(t.size===0)return;let r=nc(e.graph,e.activeNodeIds),i=[...t],a=new Set,o=0;for(;o<i.length;){let t=i[o];o++;let s=e.graph.getNode(t);if(!s)continue;let c=r.get(t);if(c)for(let t of c){if(a.has(t))continue;a.add(t);let r=e.graph.getNode(t);if(!r)continue;let o=lc(e,s,r,t,n);Object.keys(o).length>0&&e.graph.preserveSourceMetadataDuring(()=>e.graph.updateNode(t,o)),i.push(t)}}}function _c(e){let t=new Map;for(let[n,r]of e.graph.instanceIndex)for(let i of r){if(e.activeNodeIds&&!e.activeNodeIds.has(i)||e.graph.getNode(i)?.type!==`INSTANCE`)continue;let r=t.get(n);r?r.push(i):t.set(n,[i])}return t}function vc(e,t,n){let r=n.get(e);if(r)return r;let i=[],a=new Set,o=e=>{if(!a.has(e)){a.add(e),i.push(e);for(let n of t.get(e)??[])o(n)}};return o(e),n.set(e,i),i}function yc(e){return e.toLowerCase().replace(/[^a-z0-9]/g,``)}function bc(e){let[t,n]=e.split(`:`).map(Number);return{sessionID:t,localID:n}}function xc(e){return typeof e.textValue==`string`?e.textValue:e.textValue?.characters??e.textDataValue?.characters}function Sc(e){return!e||e.boolValue===void 0&&e.textValue===void 0&&e.textDataValue===void 0&&e.guidValue===void 0}function Cc(e,t,n,r){let i=t.value;if(i&&!Sc(i))return i;let a=t.varValue?.value;return a?.symbolIdValue?.guid?{guidValue:a.symbolIdValue.guid}:a?.boolValue===void 0?a?.textValue===void 0?a?.textDataValue===void 0?r?e.propDefaults.get(n)??t.value:t.value:{textDataValue:a.textDataValue}:{textValue:a.textValue}:{boolValue:a.boolValue}}function wc(e,t,n=!1){let r=new Map;for(let i of t){if(!i.defID)continue;let t=J(i.defID),a=Cc(e,i,t,n);a&&r.set(t,a)}return r}function Tc(e,t,n,r){Hs(e,n)&&r?.add(t)}function Ec(e,t,n,r){n.boolValue!==void 0&&Tc(e,t,{targetId:t,source:`component-prop`,props:{visible:n.boolValue}},r)}function Dc(e,t,n,r){let i=e.graph.getNode(t),a=xc(n);if(a===void 0||i?.type!==`TEXT`)return;let o=i.componentId?e.graph.getNode(i.componentId):null,s={text:a};o?.type===`TEXT`&&o.text===a&&(s.width=o.width,s.height=o.height,s.fills=X(o.fills),s.styleRuns=En(o.styleRuns),s.derivedTextGlyphs=o.derivedTextGlyphs?structuredClone(o.derivedTextGlyphs):void 0),Tc(e,t,{targetId:t,source:`component-prop`,props:s},r)}function Oc(e,t,n,r){let i=xc(n)??(n.guidValue?J(n.guidValue):void 0),a=i?e.guidToNodeId.get(i):void 0;if(!a)return;let o=e.graph.getNode(t)?.componentId;o&&$(e,o)===$(e,a)||Tc(e,t,{targetId:t,source:`component-prop`,swapComponentId:$(e,a)},r)}function kc(e,t,n,r,i){switch(n.componentPropNodeField){case`VISIBLE`:Ec(e,t,r,i);break;case`TEXT_DATA`:Dc(e,t,r,i);break;case`OVERRIDDEN_SYMBOL_ID`:Oc(e,t,r,i);break}}function Ac(e,t,n){let r=t;for(let t=0;r&&t<10;t++){let t=e.graph.getNode(r),i=t?.overrideKey?e.overrideKeyToGuid.get(t.overrideKey)??t.overrideKey:void 0,a=e.nodeIdToGuid.get(r)??i;if(a){let e=n.get(a);if(e)return e}let o=t?.componentId??void 0;if(o===r)break;r=o}}function jc(e,t,n){let r=yc(t),i=[];for(let t of n.keys()){let n=e.propNames.get(t);n&&yc(n)===r&&i.push({defID:bc(t),componentPropNodeField:`VISIBLE`})}return i.length>0?i:void 0}function Mc(e,t){return e.defID?t.get(J(e.defID)):void 0}function Nc(e,t,n,r,i){if(n)for(let a of n){let n=Mc(a,r);n&&kc(e,t,a,n,i)}}function Pc(e,t,n,r){if(!t)return;let i=e.graph.getNode(t);if(!i)return;let a;for(let t of i.childIds){let i=e.graph.getNode(t);if(i){if(i.id===n.componentId||i.componentId&&i.componentId===n.componentId)return Ac(e,i.id,r)??[];!a&&i.name===n.name&&i.type===n.type&&(a=i.id)}}return a?Ac(e,a,r):void 0}function Fc(e,t,n,r,i){let a=e.graph.getNode(t);if(a)for(let t of a.childIds){let o=e.graph.getNode(t);if(!o?.componentId){Fc(e,t,n,r,i);continue}Nc(e,t,Pc(e,a.componentId,o,r)??Ac(e,o.componentId,r)??jc(e,o.name,n),n,i),Fc(e,t,n,r,i)}}function Ic(e,t,n,r){for(let[i,a]of t){let t=e.guidToNodeId.get(i);!t||e.activeNodeIds&&!e.activeNodeIds.has(t)||e.graph.getNode(t)?.type===`INSTANCE`&&Fc(e,t,wc(e,a),n,r)}}function Lc(e,t,n){let r=_c(e),i=new Map;for(let[a,o]of e.changeMap){let s=e.guidToNodeId.get(a);if(!s||e.activeNodeIds&&!e.activeNodeIds.has(s)||e.graph.getNode(s)?.type!==`INSTANCE`)continue;let c=o.symbolData?.symbolOverrides;if(c)for(let a of c){if(!a.componentPropAssignments?.length)continue;let o=a.guidPath?.guids;if(!o?.length)continue;let c=wc(e,a.componentPropAssignments,!0);for(let a of vc(s,r,i)){let r=js(e,a,o);r&&Fc(e,r,c,t,n)}}}}function Rc(e){if(e.componentPropRefsMap)return e.componentPropRefsMap;let t=new Map;for(let[n,r]of e.changeMap)r.componentPropRefs?.length&&t.set(n,r.componentPropRefs);return e.componentPropRefsMap=t,t}function zc(e){if(e.componentPropAssignmentsMap)return e.componentPropAssignmentsMap;let t=new Map;for(let[n,r]of e.changeMap)r.componentPropAssignments?.length&&t.set(n,r.componentPropAssignments);return e.componentPropAssignmentsMap=t,t}function Bc(e){let t=new Set,n=Rc(e);return n.size===0?t:(Ic(e,zc(e),n,t),Lc(e,n,t),t)}function Vc(e,t,n,r,i){let a=r-n;if(i===`MAX`)return{position:e+a,size:t};if(i===`CENTER`)return{position:e+a/2,size:t};if(i===`STRETCH`)return{position:e,size:Math.max(1,t+a)};if(i===`SCALE`&&n>0){let i=r/n;return{position:e*i,size:Math.max(1,t*i)}}return{position:e,size:t}}function Hc(e,t,n,r,i){let a=Vc(e.x,e.width,t.width,n.width,r),o=Vc(e.y,e.height,t.height,n.height,i);return{x:Math.round(a.position),y:Math.round(o.position),width:Math.round(a.size),height:Math.round(o.size)}}function Uc(e){let{graph:t}=e,n=new Set;for(let r of ns(t,e.activeNodeIds)){if(r.type!==`INSTANCE`||!r.componentId)continue;let i=t.getNode(r.componentId);if(!i||i.width<=0||i.height<=0)continue;let a=Wc(t,r,i);if(!a||(Xc(e,r,a.basis),r.layoutMode!==`NONE`))continue;let{sx:o,sy:s}=a;if(Math.abs(o-1)<.001&&Math.abs(s-1)<.001)continue;let c=e.nodeIdToGuid.get(r.id),l=c?e.changeMap.get(c)?.strokeWeight:void 0;rl(t,r,i,o,s,n,e.geometryOverrideNodes,a.useCurrentChildAsSource,l,a.scaleThroughFixedWrappers)}n.size>0&&il(e,n)}function Wc(e,t,n){let r=Gc(t),i=Zc(e,t,n);if(!r&&!i)return null;let a=i??n;return{basis:a,scaleThroughFixedWrappers:r!==null,sx:t.width/a.width,sy:t.height/a.height,useCurrentChildAsSource:a!==n}}function Gc(e){let t=It(e,`targetAspectRatio`);if(!t||typeof t!=`object`||!(`value`in t))return null;let n=t.value;if(!n||typeof n!=`object`||!(`x`in n)||!(`y`in n))return null;let{x:r,y:i}=n;return typeof r!=`number`||typeof i!=`number`||!Number.isFinite(r)||!Number.isFinite(i)||r<=0||i<=0?null:{width:r,height:i}}function Kc(e,t,n){let r=t;for(let t=0;t<10&&r?.componentId;t++){if(r.componentId===n)return!0;r=e.getNode(r.componentId)}return!1}function qc(e,t,n){let r={},i=t.horizontalConstraint===`MAX`||t.horizontalConstraint===`CENTER`,a=t.verticalConstraint===`MAX`||t.verticalConstraint===`CENTER`;return i&&t.derivedLayout?.x===void 0&&!Bs(e.protectedFields,t.id,`x`)&&t.x!==n.x&&(r.x=n.x),a&&t.derivedLayout?.y===void 0&&!Bs(e.protectedFields,t.id,`y`)&&t.y!==n.y&&(r.y=n.y),r}function Jc(e,t,n){let r={};return t.horizontalConstraint===`STRETCH`&&t.derivedLayout?.width===void 0&&!Bs(e.protectedFields,t.id,`width`)&&t.width!==n.width&&(r.width=n.width),t.verticalConstraint===`STRETCH`&&t.derivedLayout?.height===void 0&&!Bs(e.protectedFields,t.id,`height`)&&t.height!==n.height&&(r.height=n.height),r}function Yc(e,t,n){return{...qc(e,t,n),...Jc(e,t,n)}}function Xc(e,t,n){let r=Math.min(t.childIds.length,n.childIds.length);for(let i=0;i<r;i++){let r=e.graph.getNode(t.childIds[i]),a=e.graph.getNode(n.childIds[i]);if(!r||!a||r.layoutPositioning!==`ABSOLUTE`||r.componentId&&!Kc(e.graph,r,a.id))continue;let o=Yc(e,r,Hc(a,n,t,r.horizontalConstraint,r.verticalConstraint));Object.keys(o).length>0&&e.graph.updateNode(r.id,o)}}function Zc(e,t,n){if(t.width!==n.width||t.height!==n.height)return n;let r=n;for(let n=0;n<10&&r.type===`INSTANCE`&&r.componentId;n++){let n=e.getNode(r.componentId);if(!n||n.width<=0||n.height<=0)break;if(t.width!==n.width||t.height!==n.height)return n;r=n}return null}function Qc(e,t,n){return e?{vertices:e.vertices.map(e=>({...e,x:e.x*t,y:e.y*n})),segments:e.segments.map(e=>({...e,tangentStart:{x:e.tangentStart.x*t,y:e.tangentStart.y*n},tangentEnd:{x:e.tangentEnd.x*t,y:e.tangentEnd.y*n}})),regions:structuredClone(e.regions)}:null}function $c(e,t,n,r,i){if(e.strokes.length!==t.strokes.length||Math.abs(n-r)>=.001)return;let a=i??1;return t.strokes.map((t,n)=>({...t,weight:e.strokes[n].weight*a}))}function el(e,t,n,r){let i={};return!r&&e.fillGeometry.length>0&&(i.fillGeometry=jn(e.fillGeometry,t,n)),!r&&e.strokeGeometry.length>0&&(i.strokeGeometry=jn(e.strokeGeometry,t,n)),e.vectorNetwork&&(i.vectorNetwork=Qc(e.vectorNetwork,t,n)),i}function tl(e,t,n){let r=n.get(t.id);if(r)return r;let i={horizontal:!1,vertical:!1};for(let r of e.getChildren(t.id)){let t=tl(e,r,n);if(i.horizontal||=r.horizontalConstraint===`SCALE`||t.horizontal,i.vertical||=r.verticalConstraint===`SCALE`||t.vertical,i.horizontal&&i.vertical)break}return n.set(t.id,i),i}function nl(e,t,n,r){let i=tl(e,t,r);return{horizontal:t.horizontalConstraint===`SCALE`||n&&i.horizontal,vertical:t.verticalConstraint===`SCALE`||n&&i.vertical}}function rl(e,t,n,r,i,a,o,s=!1,c,l=!1,u=new Map){let d=Math.min(t.childIds.length,n.childIds.length);for(let f=0;f<d;f++){let d=e.getNode(t.childIds[f]),p=e.getNode(n.childIds[f]);if(!d||!p)continue;let m=nl(e,d,l,u),h=m.horizontal,g=m.vertical;if(!h&&!g)continue;let _={},v=s?d:p;h&&(_.x=v.x*r,_.width=v.width*r),g&&(_.y=v.y*i,_.height=v.height*i);let y=h?r:1,b=g?i:1;Object.assign(_,el(v,y,b,o.has(d.id))),_.strokes=$c(v,d,y,b,c),e.updateNode(d.id,_),a.add(d.id),d.childIds.length>0&&p.childIds.length>0&&rl(e,d,p,h?r:1,g?i:1,a,o,s,c,l,u)}}function il(e,t){let{graph:n}=e,r=nc(n,e.activeNodeIds),i=[...t],a=new Set,o=0;for(;o<i.length;){let t=i[o];o++;let s=n.getNode(t);if(!s)continue;let c=r.get(t);if(c)for(let t of c){if(a.has(t))continue;a.add(t);let r=n.getNode(t);if(!r)continue;let o={};r.width!==s.width&&(o.width=s.width),r.height!==s.height&&(o.height=s.height),r.x!==s.x&&(o.x=s.x),r.y!==s.y&&(o.y=s.y),e.geometryOverrideNodes.has(t)||(s.fillGeometry.length>0&&(o.fillGeometry=On(s.fillGeometry)),s.strokeGeometry.length>0&&(o.strokeGeometry=On(s.strokeGeometry)),s.vectorNetwork&&(o.vectorNetwork=structuredClone(s.vectorNetwork))),s.strokes.length===r.strokes.length&&(o.strokes=r.strokes.map((e,t)=>({...e,weight:s.strokes[t].weight}))),Object.keys(o).length>0&&n.updateNode(t,o),i.push(t)}}}function al(e,t,n,r,i,a){let o=r.guidPath?.guids;if(!o?.length)return;let s=js(e,n,o);if(!s)return;if(s===n){a.add(n);return}let c=e.graph.getNode(s);if(!c)return;let{updates:l,hasSize:u}=ts(e,t,r,c);(r.fillGeometry?.length||r.strokeGeometry?.length)&&e.geometryOverrideNodes.add(s),Object.keys(l).length!==0&&(Hs(e,{targetId:s,source:`derived-symbol-data`,props:l})&&i.add(s),u&&a.add(s))}function ol(e){let t=new Set,n=new Set,r=new Map;for(let[i,a]of e.changeMap){if(a.type!==`INSTANCE`)continue;let o=a.derivedSymbolData;if(!o?.length)continue;let s=e.guidToNodeId.get(i);if(!(!s||e.activeNodeIds&&!e.activeNodeIds.has(s)))for(let i of o)al(e,r,s,i,t,n)}return{modified:t,sizeSet:n}}function sl(e){let{modified:t,sizeSet:n}=ol(e);gc(e,t,n)}function cl(e,t){let n=new Set,r=[...t],i=0;for(;i<r.length;){let t=r[i];if(i++,n.has(t))continue;n.add(t);let a=e.getNode(t);a&&r.push(...a.childIds)}return n}function ll(e,t){let n=new Set;function r(t){let i=e.getNode(t);if(i?.type!==`INSTANCE`||!i.componentId||i.childIds.length>0||n.has(t))return;n.add(t);let a=e.getNode(i.componentId);if(a){a.type===`INSTANCE`&&a.componentId&&a.childIds.length===0&&r(a.id);for(let t of a.childIds){let n=e.getNode(t);n?.type===`INSTANCE`&&n.componentId&&n.childIds.length===0&&r(t)}a.childIds.length>0&&i.childIds.length===0&&e.populateInstanceChildren(t,i.componentId,`fig-import`)}}if(!t){for(let t of e.nodes.values())t.type===`INSTANCE`&&t.componentId&&t.childIds.length===0&&r(t.id);return}let i=[...t],a=new Set,o=0;for(;o<i.length;){let t=i[o];if(o++,!t||a.has(t))continue;a.add(t),r(t);let n=e.getNode(t);n&&i.push(...n.childIds)}return cl(e,t)}function ul(e,t){if(e.textData!=null){let n=e.textData;n.characters!=null&&(t.text=n.characters);let r=Pa(e);r.length>0&&(t.styleRuns=r)}if(e.fillPaints!=null&&(t.fills=ra(e.fillPaints)),e.strokePaints!=null&&(t.strokes=ia(e.strokePaints,e.strokeWeight,e.strokeAlign)),e.fillPaints!=null||e.strokePaints!=null){let n=_a(e);Object.keys(n).length>0&&(t.boundVariables=n)}e.effects!=null&&(t.effects=aa(e.effects)),e.visible!=null&&(t.visible=e.visible),e.opacity!=null&&(t.opacity=e.opacity),e.name!=null&&(t.name=e.name),e.locked!=null&&(t.locked=e.locked)}function dl(e,t){if(e.size!=null){let n=e.size;n.x!=null&&(t.width=n.x),n.y!=null&&(t.height=n.y)}e.cornerRadius!=null&&(t.cornerRadius=e.cornerRadius),e.rectangleTopLeftCornerRadius!=null&&(t.topLeftRadius=e.rectangleTopLeftCornerRadius),e.rectangleTopRightCornerRadius!=null&&(t.topRightRadius=e.rectangleTopRightCornerRadius),e.rectangleBottomRightCornerRadius!=null&&(t.bottomRightRadius=e.rectangleBottomRightCornerRadius),e.rectangleBottomLeftCornerRadius!=null&&(t.bottomLeftRadius=e.rectangleBottomLeftCornerRadius),e.rectangleCornerRadiiIndependent!=null&&(t.independentCorners=e.rectangleCornerRadiiIndependent),e.arcData!=null&&(t.arcData=Za(e.arcData)),e.frameMaskDisabled!=null&&(t.clipsContent=e.frameMaskDisabled===!1)}function fl(e,t){e.stackSpacing!=null&&(t.itemSpacing=e.stackSpacing),e.stackPrimarySizing!=null&&(t.primaryAxisSizing=Ka(e.stackPrimarySizing)),e.stackCounterSizing!=null&&(t.counterAxisSizing=Ka(e.stackCounterSizing)),e.stackPrimaryAlignItems!=null&&(t.primaryAxisAlign=qa(e.stackPrimaryAlignItems)),e.stackCounterAlignItems!=null&&(t.counterAxisAlign=Ja(e.stackCounterAlignItems)),e.stackChildPrimaryGrow!=null&&(t.layoutGrow=e.stackChildPrimaryGrow),e.stackChildAlignSelf!=null&&(t.layoutAlignSelf=Ya(e.stackChildAlignSelf)),e.stackPositioning!=null&&(t.layoutPositioning=e.stackPositioning===`ABSOLUTE`?`ABSOLUTE`:`AUTO`),e.stackVerticalPadding!=null&&(t.paddingTop=e.stackVerticalPadding,e.stackPaddingBottom??(t.paddingBottom=e.stackVerticalPadding)),e.stackHorizontalPadding!=null&&(t.paddingLeft=e.stackHorizontalPadding,e.stackPaddingRight??(t.paddingRight=e.stackHorizontalPadding)),e.stackPaddingBottom!=null&&(t.paddingBottom=e.stackPaddingBottom),e.stackPaddingRight!=null&&(t.paddingRight=e.stackPaddingRight)}function pl(e,t){if(e.strokeWeight!=null&&!e.strokePaints&&t.strokes)for(let n of t.strokes)n.weight=e.strokeWeight;if(e.strokeAlign!=null&&t.strokes){let n=`CENTER`;e.strokeAlign===`INSIDE`?n=`INSIDE`:e.strokeAlign===`OUTSIDE`&&(n=`OUTSIDE`);for(let e of t.strokes)e.align=n}e.borderTopWeight!=null&&(t.borderTopWeight=e.borderTopWeight),e.borderRightWeight!=null&&(t.borderRightWeight=e.borderRightWeight),e.borderBottomWeight!=null&&(t.borderBottomWeight=e.borderBottomWeight),e.borderLeftWeight!=null&&(t.borderLeftWeight=e.borderLeftWeight),e.borderStrokeWeightsIndependent!=null&&(t.independentStrokeWeights=e.borderStrokeWeightsIndependent)}function ml(e,t){if(e.fontName!=null){let n=e.fontName;n.family&&(t.fontFamily=n.family),n.style&&(t.fontWeight=Mr(n.style),t.italic=n.style.toLowerCase().includes(`italic`))}e.fontSize!=null&&(t.fontSize=e.fontSize),e.textAlignHorizontal!=null&&(t.textAlignHorizontal=e.textAlignHorizontal),e.textAutoResize!=null&&(t.textAutoResize=e.textAutoResize),e.lineHeight!=null&&(t.lineHeight=Oa(e.lineHeight,e.fontSize)),e.letterSpacing!=null&&(t.letterSpacing=ka(e.letterSpacing,e.fontSize)),e.maxLines!=null&&(t.maxLines=e.maxLines),e.textTruncation!=null&&(t.textTruncation=e.textTruncation===`ENDING`?`ENDING`:`DISABLED`),e.textDecoration!=null&&(t.textDecoration=Da(e.textDecoration))}function hl(e){let t={};return ul(e,t),dl(e,t),fl(e,t),pl(e,t),ml(e,t),t}let gl=new Set([`RECTANGLE_TOP_LEFT_CORNER_RADIUS`,`RECTANGLE_TOP_RIGHT_CORNER_RADIUS`,`RECTANGLE_BOTTOM_LEFT_CORNER_RADIUS`,`RECTANGLE_BOTTOM_RIGHT_CORNER_RADIUS`]);function _l(e){return e.version?`${e.key}@${e.version}`:e.key}function vl(e,t){if(e.guid)return J(e.guid);let n=e.assetRef;if(n?.key)return t.get(_l(n))??t.get(n.key)}function yl(e,t,n,r=0){if(r>10)return;let i=e.changeMap.get(t)?.variableDataValues?.entries?.[0];if(!i)return;let a=i.variableData.value;if(!a)return;if(typeof a.floatValue==`number`)return a.floatValue;let o=a.alias,s=o?vl(o,n):void 0;return s?yl(e,s,n,r+1):void 0}function bl(e,t,n){let r=t.variableConsumptionMap?.entries;if(!r?.length)return;let i=e.assetRefToGuid;for(let t of r){let r=t.variableField;if(!r||!gl.has(r))continue;let a=t.variableData?.value?.alias,o=a?vl(a,i):void 0,s=o?yl(e,o,i):void 0;if(typeof s!=`number`)continue;let c=ua[r];c===`topLeftRadius`?n.topLeftRadius=s:c===`topRightRadius`?n.topRightRadius=s:c===`bottomRightRadius`?n.bottomRightRadius=s:c===`bottomLeftRadius`&&(n.bottomLeftRadius=s)}}function xl(e,t,n){let r={targetId:t,source:`symbol-override`};if(n.overriddenSymbolID){let t=J(n.overriddenSymbolID);r.swapComponentId=e.guidToNodeId.get(t)}let i={...n};if(delete i.guidPath,delete i.overriddenSymbolID,delete i.componentPropAssignments,Object.keys(i).length>0){Yo(e.changeMap,i);let t=hl(i);bl(e,i,t),Object.keys(t).length>0&&(r.props=t)}return r.swapComponentId||r.props?r:null}function Sl(e,t,n){if(!n?.props)return;let r=Object.fromEntries(Object.entries(n.props).filter(([n])=>!hr(e.graph,t,n)));n.props=Object.keys(r).length>0?r:void 0}function Cl(e,t){return t!==void 0&&(!e.activeNodeIds||e.activeNodeIds.has(t))}function wl(e,t,n,r){!e||n!==t||!r?.props||(delete r.props.width,delete r.props.height)}function Tl(e,t=!1){let n=new Set;e.componentIdRoot.clear();for(let[r,i]of e.changeMap){if(i.type!==`INSTANCE`)continue;let a=i.symbolData?.symbolOverrides;if(!a?.length)continue;let o=e.guidToNodeId.get(r);if(Cl(e,o))for(let r of a){let a=r.guidPath?.guids;if(!a?.length)continue;let s=js(e,o,a);if(!s||s===o&&e.kiwiPropertyNodes.has(o))continue;let c=xl(e,s,r);c&&(Sl(e,s,c),wl(i.size!==void 0,o,s,c),t&&(c.swapComponentId=void 0),!(!c.swapComponentId&&!c.props)&&(n.add(s),Hs(e,c)))}}return n}function*El(e,t){for(let[n,r]of t){let t=e.get(n);t&&(yield[r,t])}}function Dl(e,t,n){let r=new Set;for(let[i,a]of El(t,n)){let t=a,n=e.getNode(i);if(!n?.componentId)continue;let o=e.getNode(n.componentId);if(!o)continue;let s=(t.cornerRadius!==void 0||t.rectangleCornerRadiiIndependent!==void 0)&&n.cornerRadius!==o.cornerRadius,c=t.visible===!1&&o.visible,l=t.fillPaints!==void 0&&!Jt(n.fills,o.fills),u=t.strokePaints!==void 0&&!Jt(n.strokes,o.strokes),d=t.textData!==void 0&&n.type===`TEXT`&&o.type===`TEXT`&&n.text!==o.text;(s||c||l||u||d)&&r.add(i)}return r}function Ol(e,t){let n=new Set;for(let[r,i]of El(e,t))(i.fillGeometry?.length||i.strokeGeometry?.length)&&n.add(r);return n}function kl(e){let t=[];for(let n of e.getAllNodes())n.componentId&&t.push(n);return t}function Al(e){let t=[];for(let n of e.getAllNodes()){if(n.type!==`INSTANCE`||!n.componentId)continue;let r=e.getNode(n.componentId);if(!(!r||r.childIds.length!==n.childIds.length))for(let e=0;e<n.childIds.length;e++)t.push({sourceChildId:r.childIds[e],childId:n.childIds[e]})}return t}function jl(e,t,n=kl(e)){for(let r=0;r<10;r++){let r=!1;for(let i of n){if(!i.componentId)continue;let n=e.getNode(i.componentId);!n||Jt(n.fills,i.fills)||t.has(i.id)&&!t.has(n.id)||hr(e,i.id,`fills`)||(e.updateNode(i.id,{fills:X(n.fills)}),r=!0)}if(!r)return}}function Ml(e,t=Al(e)){for(let n=0;n<10;n++){let n=!1;for(let r of t){let t=e.getNode(r.sourceChildId),i=e.getNode(r.childId);if(!t||!i||t.overrideKey&&i.overrideKey&&t.overrideKey!==i.overrideKey)continue;let a={};!t.visible&&i.visible&&(a.visible=!1),t.x!==i.x&&(a.x=t.x),t.y!==i.y&&(a.y=t.y),Object.keys(a).length!==0&&(e.updateNode(i.id,a),n=!0)}if(!n)return}}function Nl(e,t){return e===t?!0:!e||!t?!1:Sn(e,t)}function Pl(e,t){let n=[],r=new Set,i=new Set,a=t=>{if(r.has(t.id)||i.has(t.id))return;i.add(t.id);let o=t.componentId?e.getNode(t.componentId):void 0;o?.type===`TEXT`&&a(o),i.delete(t.id),r.add(t.id),t.type===`TEXT`&&t.componentId&&n.push(t)};for(let n of t??e.nodes.keys()){let t=e.getNode(n);t?.type===`TEXT`&&t.componentId&&a(t)}for(let t of n){let n=t.componentId?e.getNode(t.componentId):void 0;n?.type!==`TEXT`||n.text!==t.text||n.width===t.width&&n.height===t.height&&Jt(n.fills,t.fills)&&Jt(n.styleRuns,t.styleRuns)&&Nl(n.derivedTextGlyphs,t.derivedTextGlyphs)||e.updateNode(t.id,{width:n.width,height:n.height,fills:X(n.fills),styleRuns:En(n.styleRuns),derivedTextGlyphs:n.derivedTextGlyphs?Y(n.derivedTextGlyphs,structuredClone(n.derivedTextGlyphs)):void 0})}}function Fl(e,t,n,r,i){let a=new Map,o=new Map;for(let[e,n]of t)n.overrideKey&&a.set(J(n.overrideKey),e),typeof n.key==`string`&&(o.set(n.key,e),typeof n.version==`string`&&o.set(`${n.key}@${n.version}`,e));let s=new Map,c=new Map;for(let[,e]of t)if(e.componentPropDefs?.length)for(let t of e.componentPropDefs){if(!t.id)continue;let e=J(t.id);t.initialValue&&s.set(e,t.initialValue),t.name&&c.set(e,t.name)}let l=new Map;for(let[e,t]of n)l.set(t,e);let u=Dl(e,t,n),d=Ol(t,n);return{graph:e,changeMap:t,guidToNodeId:n,blobs:r,overrideKeyToGuid:a,assetRefToGuid:o,nodeIdToGuid:l,propDefaults:s,propNames:c,preComputedRoot:new Map,preComputedClones:new Map,componentIdRoot:new Map,swappedInstances:new Set,protectedFields:new Map,kiwiPropertyNodes:u,geometryOverrideNodes:d,activeNodeIds:i}}function Il(e,t){for(let n of ns(e,t)){let t={};for(let[r,i]of Object.entries(n.boundVariables)){if(Array.isArray(i))continue;let a=e.resolveNumberVariableForNode(n.id,i);a!==void 0&&Object.assign(t,pa(r,a))}Object.keys(t).length>0&&e.updateNode(n.id,t)}}function Ll(e,t,n,r=[],i){let a=Fl(e,t,n,r,ll(e,i));xs(a);let o=Tl(a);for(let e of a.kiwiPropertyNodes)o.add(e);sc(e,o,a.swappedInstances,a.componentIdRoot,void 0,a.activeNodeIds,a.protectedFields);let s=Bc(a);if(s.size>0&&sc(e,s,a.swappedInstances,a.componentIdRoot,o,a.activeNodeIds,a.protectedFields),i){let t=ll(e,i);t&&(a.activeNodeIds=t,cs(e,t,a.preComputedClones));let n=Bc(a),r=new Set([...o,...s,...n]);r.size>0&&sc(e,r,a.swappedInstances,a.componentIdRoot,o,a.activeNodeIds,a.protectedFields),Ml(e)}sl(a),jl(e,new Set([...a.kiwiPropertyNodes,...o])),Pl(e,a.activeNodeIds),Uc(a);let c=new Set;for(let t of ns(e,a.activeNodeIds)){if(t.type!==`INSTANCE`||!t.componentId)continue;let n=e.getNode(t.componentId);n&&(t.width!==n.width||t.height!==n.height)&&c.add(t.id)}Bc(a),oc(e,Tl(a,!0),a.activeNodeIds,a.protectedFields,a.preComputedClones),uc(a,c),Il(e,a.activeNodeIds),hc(a)}self.location.href,typeof window<`u`&&`__TAURI_INTERNALS__`in window;let Rl={r:0,g:0,b:0,a:1};[{id:`harness:pi`,name:`Pi`,keyPlaceholder:`Provider API key`,keyURL:``,defaultModel:``,supportsCustomModel:!0,models:[]},{id:`openrouter`,name:`OpenRouter`,keyPlaceholder:`sk-or-…`,keyURL:`https://openrouter.ai/keys`,defaultModel:`anthropic/claude-sonnet-5`,supportsCustomModel:!0,models:[{id:`anthropic/claude-sonnet-5`,name:`Claude Sonnet 5`,tag:`Best for design`,capabilities:[`tools`,`vision`]},{id:`anthropic/claude-opus-5`,name:`Claude Opus 5`,tag:`Smartest`,capabilities:[`tools`,`vision`]},{id:`anthropic/claude-fable-5.1`,name:`Claude Fable 5.1`,tag:`Latest Anthropic`,capabilities:[`tools`,`vision`]},{id:`openai/gpt-5.6`,name:`GPT-5.6`,tag:`Latest OpenAI`,capabilities:[`tools`,`vision`]},{id:`google/gemini-3.8-flash`,name:`Gemini 3.8 Flash`,tag:`Fast`,capabilities:[`tools`,`vision`]},{id:`z-ai/glm-5.3`,name:`GLM-5.3`,capabilities:[`tools`]},{id:`deepseek/deepseek-v4-pro`,name:`DeepSeek V4 Pro`,tag:`Reasoning`,capabilities:[`tools`]},{id:`moonshotai/kimi-k3`,name:`Kimi K3`,tag:`Vision + code`,capabilities:[`tools`,`vision`]},{id:`qwen/qwen3-coder:free`,name:`Qwen3 Coder`,tag:`Free`},{id:`openai/gpt-oss-120b:free`,name:`GPT-OSS 120B`,tag:`Free`}]},{id:`anthropic`,name:`Anthropic`,keyPlaceholder:`sk-ant-…`,keyURL:`https://console.anthropic.com/settings/keys`,defaultModel:`claude-sonnet-5`,models:[{id:`claude-sonnet-5`,name:`Claude Sonnet 5`,tag:`Best for design`,capabilities:[`tools`,`vision`]},{id:`claude-opus-5`,name:`Claude Opus 5`,tag:`Smartest`,capabilities:[`tools`,`vision`]},{id:`claude-fable-5-1`,name:`Claude Fable 5.1`,tag:`Latest`,capabilities:[`tools`,`vision`]}]},{id:`openai`,name:`OpenAI`,keyPlaceholder:`sk-…`,keyURL:`https://platform.openai.com/api-keys`,defaultModel:`gpt-5.6`,models:[{id:`gpt-5.6`,name:`GPT-5.6`,tag:`Best`,capabilities:[`tools`,`vision`]},{id:`gpt-5.5`,name:`GPT-5.5`,capabilities:[`tools`,`vision`]},{id:`gpt-5.4-mini`,name:`GPT-5.4 mini`,tag:`Fast`,capabilities:[`tools`,`vision`]},{id:`gpt-5.4-nano`,name:`GPT-5.4 nano`,tag:`Cheap`,capabilities:[`tools`,`vision`]}]},{id:`google`,name:`Google AI`,keyPlaceholder:`AIza…`,keyURL:`https://aistudio.google.com/apikey`,defaultModel:`gemini-3.8-flash`,models:[{id:`gemini-3.8-flash`,name:`Gemini 3.8 Flash`,tag:`Latest`,capabilities:[`tools`,`vision`]},{id:`gemini-3.7-flash`,name:`Gemini 3.7 Flash`,capabilities:[`tools`,`vision`]},{id:`gemini-flash-latest`,name:`Gemini Flash Latest`,tag:`Alias`,capabilities:[`tools`,`vision`]},{id:`gemini-3.1-pro-preview`,name:`Gemini 3.1 Pro`,tag:`Pro`,capabilities:[`tools`,`vision`]}]},{id:`deepseek`,name:`DeepSeek`,keyPlaceholder:`sk-…`,keyURL:`https://platform.deepseek.com/api_keys`,defaultModel:`deepseek-v4-flash`,models:[{id:`deepseek-v4-flash`,name:`DeepSeek V4 Flash`,tag:`Fast`},{id:`deepseek-v4-pro`,name:`DeepSeek V4 Pro`,tag:`Reasoning`}]},{id:`zai`,name:`Z.ai`,keyPlaceholder:`API key`,keyURL:`https://docs.z.ai/devpack/quick-start`,defaultModel:`glm-5.3`,models:[{id:`glm-5.3`,name:`GLM-5.3`,tag:`Best`},{id:`glm-5.3-flash`,name:`GLM-5.3-Flash`,tag:`Fast`},{id:`glm-5.2`,name:`GLM-5.2`},{id:`glm-5v-turbo`,name:`GLM-5V-Turbo`,tag:`Vision`,capabilities:[`tools`,`vision`]},{id:`glm-5.1`,name:`GLM-5.1`},{id:`glm-5`,name:`GLM-5`},{id:`glm-5-code`,name:`GLM-5-Code`},{id:`glm-4.7`,name:`GLM-4.7`},{id:`glm-4.7-flashx`,name:`GLM-4.7-FlashX`},{id:`glm-4.6`,name:`GLM-4.6`},{id:`glm-4.5`,name:`GLM-4.5`},{id:`glm-4.5-x`,name:`GLM-4.5-X`},{id:`glm-4.5-air`,name:`GLM-4.5-Air`},{id:`glm-4.5-airx`,name:`GLM-4.5-AirX`},{id:`glm-4-32b-0414-128k`,name:`GLM-4-32B-0414-128K`},{id:`glm-4.7-flash`,name:`GLM-4.7-Flash`,tag:`Free`},{id:`glm-4.5-flash`,name:`GLM-4.5-Flash`,tag:`Free`}]},{id:`minimax`,name:`MiniMax`,keyPlaceholder:`API key`,keyURL:`https://platform.minimax.io/user-center/basic-information/interface-key`,defaultModel:`MiniMax-M3`,models:[{id:`MiniMax-M3`,name:`MiniMax-M3`,tag:`Best`},{id:`MiniMax-M2.7`,name:`MiniMax-M2.7`},{id:`MiniMax-M2.7-highspeed`,name:`MiniMax-M2.7-highspeed`,tag:`Fast`},{id:`MiniMax-M2.5`,name:`MiniMax-M2.5`},{id:`MiniMax-M2.5-highspeed`,name:`MiniMax-M2.5 Highspeed`,tag:`Fast`},{id:`MiniMax-M2.1`,name:`MiniMax-M2.1`},{id:`MiniMax-M2.1-highspeed`,name:`MiniMax-M2.1 Highspeed`,tag:`Fast`},{id:`MiniMax-M2`,name:`MiniMax-M2`}]},{id:`openai-compatible`,name:`OpenAI-compatible`,keyPlaceholder:`API key`,keyURL:``,defaultModel:``,models:[],supportsCustomBaseURL:!0,supportsCustomModel:!0},{id:`anthropic-compatible`,name:`Anthropic-compatible`,keyPlaceholder:`API key`,keyURL:``,defaultModel:``,models:[],supportsCustomBaseURL:!0,supportsCustomModel:!0}].find(e=>e.id===`openai-compatible`)?.defaultModel;let zl=new WeakMap;function Bl(e,t){zl.set(e,t)}function Vl(e){return zl.get(e)}function Hl(e,t,n){e.preserveSourceMetadataDuring(()=>{Ll(e,t.changeMap,t.guidToNodeId,t.blobs,n)});let r=n??e.getPages(!0).map(e=>e.id);for(let e of r)t.populatedRootIds.add(e)}function Ul(e,t,n){let r=[...n].filter(e=>e&&!t.populatedRootIds.has(e));return r.length===0?!1:(Hl(e,t,r),!0)}function Wl(e,t){let n=Vl(e);return n?Ul(e,n,t):!1}function Gl(e,t){e.source.format=`fig`,e.source.orderKey=t.parentIndex?.position??null,t.backgroundColor&&(e.source.fig.rawNodeFields.backgroundColor=structuredClone(t.backgroundColor)),t.backgroundPaints&&(e.source.fig.rawNodeFields.backgroundPaints=structuredClone(t.backgroundPaints)),t.guides&&(e.guides=Qt(t.guides),e.source.fig.rawNodeFields.guides=structuredClone(t.guides)),e.source.fig.rawNodeFields.strokeJoin=t.strokeJoin,e.source.fig.rawNodeFields.strokeWeight=t.strokeWeight,t.pageType&&(e.source.fig.rawNodeFields.pageType=t.pageType)}function Kl(e,t){let n=e.getNode(e.rootId);if(!t||!n)return;n.source.format=`fig`,n.pluginData=t.pluginData?t.pluginData.map(e=>({pluginId:e.pluginID,key:e.key,value:e.value})):[],n.source.fig.rawNodeFields.strokeJoin=t.strokeJoin,n.source.fig.rawNodeFields.strokeWeight=t.strokeWeight;let r=Ta(t,`enabledLibraries`);if(r)try{let t=JSON.parse(r);if(!Array.isArray(t))return;for(let n of t){if(!n||typeof n!=`object`||Array.isArray(n))continue;let t=n;typeof t.libraryId!=`string`||typeof t.revisionId!=`string`||e.enabledLibraries.set(t.libraryId,{libraryId:t.libraryId,revisionId:t.revisionId,enabled:t.enabled===!0})}}catch(e){console.warn(`Ignored malformed OpenPencil library metadata`,e)}}function ql(e){return e.version?`${e.key}@${e.version}`:e.key}function Jl(e){let t=new Map;for(let[n,r]of e)typeof r.key==`string`&&((typeof r.version!=`string`||!t.has(r.key))&&t.set(r.key,n),typeof r.version==`string`&&t.set(ql({key:r.key,version:r.version}),n),typeof r.userFacingVersion==`string`&&t.set(ql({key:r.key,version:r.userFacingVersion}),n));return t}function Yl(e,t){if(e.guid)return J(e.guid);if(e.assetRef)return t.get(ql(e.assetRef))??t.get(e.assetRef.key)}function Xl(e,t){let n=new Map,r=new Map;for(let[t,i]of e){if(i.type!==`VARIABLE`)continue;n.set(t,i.variableDataValues?.entries??[]);let e=i.variableSetID?.guid?J(i.variableSetID.guid):void 0,a=i.parentIndex?.guid?J(i.parentIndex.guid):void 0;e?r.set(t,e):a&&r.set(t,a)}let i=new Map;for(let[t,n]of e){if(n.type!==`VARIABLE_SET`)continue;let e=n.variableSetModes??[];e.length>0&&i.set(t,J(e[0].id))}function a(e,o,s){if(s>10)return null;let c=n.get(e);if(!c?.length)return null;let l=r.get(e),u=l?i.get(l):void 0,d=o?c.find(e=>J(e.modeID)===o):void 0;!d&&u&&(d=c.find(e=>J(e.modeID)===u)),d||=c[0];let f=d.variableData.value;if(!f)return null;if(f.colorValue)return f.colorValue;if(f.alias){let e=Yl(f.alias,t);if(e)return a(e,J(d.modeID),s+1)}return null}return function(e){let n=Yl(e,t);return n?a(n,void 0,0):null}}function Zl(e){let t=new Map,n=new Map,r=new Map;for(let i of e){if(!i.guid||i.phase===`REMOVED`)continue;let e=J(i.guid);if(t.set(e,i),i.parentIndex?.guid){let t=J(i.parentIndex.guid);n.set(e,t);let a=r.get(t);a||(a=[],r.set(t,a)),a.push(e)}}for(let[e,n]of r){let r=t.get(e);r&&Io(n,r,t)}return{changeMap:t,parentMap:n,childrenMap:r}}function Ql(e){return e===`COLOR`?`COLOR`:e===`BOOLEAN`?`BOOLEAN`:e===`STRING`?`STRING`:`FLOAT`}function $l(e,t){let n=e.variableData;if(!n.value)return;let r=n.dataType??n.resolvedDataType;if(r===`COLOR`&&n.value.colorValue){let e=n.value.colorValue;return{r:e.r,g:e.g,b:e.b,a:e.a}}if(r===`BOOLEAN`)return n.value.boolValue??!1;if(r===`STRING`)return n.value.textValue??``;if(r===`ALIAS`&&n.value.alias){let e=Yl(n.value.alias,t);return e?{aliasId:e}:void 0}return n.value.floatValue??0}function eu(e){return e===`BOOLEAN`?!1:e===`STRING`?``:e===`COLOR`?{...Rl}:0}function tu(e,t){for(let[n,r]of e){if(r.type!==`VARIABLE_SET`)continue;let e=(r.variableSetModes??[]).map(e=>({modeId:J(e.id),name:e.name}));e.length===0&&e.push({modeId:`default`,name:`Default`}),t.addCollection({id:n,name:r.name??`Variables`,modes:e,defaultModeId:e[0].modeId,variableIds:[]})}}function nu(e,t,n,r){if(e.variableSetID?.guid)return J(e.variableSetID.guid);let i=e.variableSetID?.assetRef;return i?r.get(ql(i))??r.get(i.key)??``:n.get(t)??``}function ru(e,t,n){if(t.variableCollections.has(n))return;let r=e.get(n);t.addCollection({id:n,name:r?.name??`Variables`,modes:[{modeId:`default`,name:`Default`}],defaultModeId:`default`,variableIds:[]})}function iu(e,t,n,r){for(let[i,a]of e){if(a.type!==`VARIABLE`)continue;let o=nu(a,i,t,r);ru(e,n,o);let s=Ql(a.variableResolvedType),c={};if(a.variableDataValues?.entries)for(let e of a.variableDataValues.entries){let t=$l(e,r);t!==void 0&&(c[J(e.modeID)]=t)}if(Object.keys(c).length===0){let e=n.variableCollections.get(o)?.defaultModeId??`default`;c[e]=eu(s)}n.addVariable({id:i,name:a.name??`Variable`,type:s,collectionId:o,valuesByMode:c,description:``,hiddenFromPublishing:!1,key:typeof a.key==`string`?a.key:void 0,version:typeof a.version==`string`?a.version:void 0})}}function au(e,t,n,r,i,a,o){let s=null;for(let[e,n]of t)if(n.type===`DOCUMENT`||e===`0:0`){s=e;break}if(s){Kl(e,t.get(s));for(let n of r.get(s)??[]){let s=t.get(n);if(s)if(s.type===`CANVAS`){let t=e.addPage(s.name??`Page`);t.source.id=n,Gl(t,s),a.set(n,t.id),s.internalOnly&&(t.internalOnly=!0),i.add(n);for(let e of r.get(n)??[])o(e,t.id)}else o(n,e.getPages()[0]?.id??e.rootId)}}else{let r=[];for(let[e]of t){let i=n.get(e);(!i||!t.has(i))&&r.push(e)}let i=e.getPages()[0]??e.addPage(`Page 1`);for(let e of r)o(e,i.id)}}function ou(e,t,n){for(let[r,i]of e){if(!i.variableConsumptionMap?.entries?.length)continue;let e=t.get(r);if(e)for(let t of i.variableConsumptionMap.entries){let r=da(t);r&&n.bindVariable(e,r.field,r.variableId)}}}function su(e,t){e.preserveSourceMetadataDuring(()=>{for(let n of e.getAllNodes()){if(n.type!==`INSTANCE`||!n.componentId)continue;let r=t.get(n.componentId);r&&e.updateNode(n.id,{componentId:r})}})}function cu(e,t){let n=new Map;for(let t of e.getAllNodes())for(let e of t.componentPropertyDefinitions)n.has(e.id)||n.set(e.id,e);e.preserveSourceMetadataDuring(()=>{for(let r of e.getAllNodes()){if(r.componentPropertyDefinitions.length>0){let n=r.componentPropertyDefinitions.map(e=>{if(e.type!==`INSTANCE_SWAP`)return e;let n=e.defaultValue?t.get(e.defaultValue):void 0;return n?{...e,defaultValue:n}:e});n.some((e,t)=>e!==r.componentPropertyDefinitions[t])&&e.updateNode(r.id,{componentPropertyDefinitions:n})}if(Object.keys(r.componentPropertyAssignments).length>0){let i=!1,a={...r.componentPropertyAssignments};for(let[e,r]of Object.entries(a)){if(n.get(e)?.type!==`INSTANCE_SWAP`)continue;let o=t.get(r);o&&(a[e]=o,i=!0)}i&&e.updateNode(r.id,{componentPropertyAssignments:a})}}})}function lu(e){for(let t of e.getAllNodes()){if(t.type!==`COMPONENT`||t.variantPropSpecs.length===0||!t.parentId)continue;let n=e.getNode(t.parentId);if(n?.type!==`COMPONENT_SET`)continue;let r=new Map(n.componentPropertyDefinitions.map(e=>[e.id,e.name])),i={};for(let e of t.variantPropSpecs)i[r.get(e.propDefId)??e.propDefId]=e.value;e.updateNode(t.id,{componentPropertyValues:i})}}function uu(e){return e.find(e=>e.type===`DOCUMENT`)?.documentColorProfile===`DISPLAY_P3`?`display-p3`:`srgb`}function du(e,t){for(let n of e.values())Yo(e,n,t)}function fu(e,t,n,r,i){Bl(e,{changeMap:t,guidToNodeId:n,blobs:r,populatedRootIds:new Set(i)})}function pu(e){let t=new Set;for(let n of e.getAllNodes()){if(n.type!==`COMPONENT`&&n.type!==`COMPONENT_SET`)continue;let r=n.parentId?e.getNode(n.parentId):void 0;for(;r?.parentId&&r.type!==`CANVAS`;)r=e.getNode(r.parentId);r?.type===`CANVAS`&&t.add(r.id)}return t}function mu(e,t=[],n,r={}){let i=new Ii;if(i.documentColorSpace=uu(e),n)for(let[e,t]of n)i.images.set(e,t);for(let e of i.getPages(!0))i.deleteNode(e.id);let{changeMap:a,parentMap:o,childrenMap:s}=Zl(e),c=Jl(a);du(a,c),Xi(Xl(a,c));let l=new Map,u=new Set,d=new Map,f=e=>s.get(e)??[];function p(e,n){if(u.has(e))return;u.add(e);let r=a.get(e);if(!r)return;let{nodeType:s,...c}=yo(r,t);if(c.sharedStyleType&&(c.internalOnly=!0),s===`DOCUMENT`||s===`VARIABLE`||r.type===`VARIABLE_SET`)return;vo(r,a.get(o.get(e)??``))&&(c.textAutoResize=`WIDTH_AND_HEIGHT`);let h=l.get(n)??n,g=i.createNode(s,h,c);d.set(e,g.id);for(let t of f(e)){if(s===`INSTANCE`&&a.get(t)?.isSlotContent){m.push({ncId:t,instanceId:g.id});continue}p(t,g.id)}}let m=[];au(i,a,o,s,u,l,p),tu(a,i),iu(a,o,i,c),ou(a,d,i),su(i,d),cu(i,d),lu(i);let h=i.getPages().find(e=>!e.internalOnly)?.id,g=r.populate===`first-page`?pu(i):new Set,_=r.populate===`first-page`?[h,...g].filter(Yt):void 0;return r.populate!==`none`&&i.preserveSourceMetadataDuring(()=>{Ll(i,a,d,t,_)}),Uo(i),m.length>0&&hu(i,m,a,d,t,p),_&&fu(i,a,d,t,_),Xi(null),i.getPages(!0).length===0&&i.addPage(`Page 1`),i}function hu(e,t,n,r,i,a){let o=new Map,s=[];for(let{ncId:c,instanceId:l}of t){let t=e.getNode(l),u=n.get(c);if(!t||!u)continue;let d=Kn({pluginData:(u.pluginData??[]).map(e=>({pluginId:e.pluginID,key:e.key,value:e.value}))});if(!d)continue;t.childIds.length===0&&(e.preserveSourceMetadataDuring(()=>{Ll(e,n,r,i,[l])}),Uo(e));let f=`${l}\u0000${d}`;o.has(f)||o.set(f,qn(e,t,d));let p=o.get(f);if(!p)continue;a(c,p.id);let m=r.get(c),h=m?e.getNode(m):void 0;h&&(h.pluginData=h.pluginData.filter(e=>Kn({pluginData:[e]})===null),s.push(h.id))}s.length&&e.preserveSourceMetadataDuring(()=>{Ll(e,n,r,i,s)})}function gu(e){let t=Vl(e);return{rootId:e.rootId,nodes:[...e.nodes],images:[...e.images],variables:[...e.variables],variableCollections:[...e.variableCollections],activeMode:[...e.activeMode],instanceIndex:[...e.instanceIndex].map(([e,t])=>[e,[...t]]),figKiwiVersion:e.figKiwiVersion,figSchemaDeflated:e.figSchemaDeflated,documentColorSpace:e.documentColorSpace,enabledLibraries:[...e.enabledLibraries],lazyFigImport:t?{changeMap:[...t.changeMap],guidToNodeId:[...t.guidToNodeId],blobs:t.blobs,populatedRootIds:[...t.populatedRootIds]}:void 0}}function _u(e){let t=new Set(e.nodes.keys()),n=new Map,r=new Set,i=new Set,a={createNode:e.createNode.bind(e),createNodeWithId:e.createNodeWithId.bind(e),updateNode:e.updateNode.bind(e),deleteNode:e.deleteNode.bind(e)};function o(t,i){if(!t||r.has(t))return;let a=e.getNode(t);if(!a)return;let o=n.get(t)??{};n.set(t,o);for(let e of i)e in o||Object.assign(o,{[e]:structuredClone(a[e])})}return e.createNode=((e,t,n)=>{o(t,[`childIds`]);let i=a.createNode(e,t,n);return r.add(i.id),i}),e.createNodeWithId=((e,t,n,i)=>{o(n,[`childIds`]);let s=a.createNodeWithId(e,t,n,i);return r.add(s.id),s}),e.updateNode=((t,n)=>{let r=e.getNode(t);if(r){let e=Object.keys(n).filter(e=>!Jt(r[e],n[e]));e.length>0&&e.push(`source`),`componentId`in n&&e.push(`componentId`),(`fills`in n||`strokes`in n)&&e.push(`boundVariables`),o(t,e)}a.updateNode(t,n)}),e.deleteNode=(s=>{let c=e.getNode(s);o(c?.parentId,[`childIds`]);let l=c?[s]:[];for(;l.length>0;){let a=l.pop();if(!a)continue;let o=e.getNode(a);o&&l.push(...o.childIds),t.has(a)?i.add(a):(r.delete(a),n.delete(a))}a.deleteNode(s)}),{before:n,created:r,deleted:i,stop(){e.createNode=a.createNode,e.createNodeWithId=a.createNodeWithId,e.updateNode=a.updateNode,e.deleteNode=a.deleteNode}}}function vu(e,t,n){let r=[];for(let[n,i]of t.before){let a=e.getNode(n);if(!a||t.deleted.has(n))continue;let o={};for(let e of Object.keys(i))Jt(i[e],a[e])||Object.assign(o,{[e]:structuredClone(a[e])});Object.keys(o).length>0&&r.push([n,o])}return{created:[...t.created].map(t=>e.getNode(t)).filter(e=>e!==void 0).map(e=>[e.id,structuredClone(e)]),updated:r,deleted:[...t.deleted],instanceIndex:[...e.instanceIndex].map(([e,t])=>[e,[...t]]),populatedRootIds:[...n]}}let yu,bu,xu;function Su(e){xu?.postMessage(e)}function Cu(e){if(!yu)throw Error(`FIG session has no retained graph`);let t=_u(yu);try{let n=Wl(yu,[e.pageId]),r=Vl(yu);if(!r)throw Error(`FIG session has no lazy import context`);Su({type:`population-result`,requestId:e.requestId,baseRevision:e.baseRevision,populated:n,delta:vu(yu,t,r.populatedRootIds)})}finally{t.stop()}}function wu(e){try{if(e.type===`original-archive`){if(!bu)throw Error(`FIG session has no original archive`);let t=bu.slice();xu?.postMessage({type:`original-archive-result`,requestId:e.requestId,bytes:t},[t.buffer]);return}if(e.type===`dispose`){yu=void 0,bu=void 0,Su({type:`disposed`}),xu?.close(),xu=void 0,self.close();return}if(e.type===`cancel`)return;Cu(e)}catch(t){Su({type:`population-error`,requestId:e.type===`populate`?e.requestId:void 0,error:t instanceof Error?t.message:String(t)})}}self.onmessage=e=>{let t=e.data;xu=t.port,xu.onmessage=e=>wu(e.data),xu.start(),bu=new Uint8Array(t.archiveBuffer);try{let{nodeChanges:e,blobs:n,images:r,figKiwiVersion:i,figSchemaDeflated:a}=Nt(t.originalBuffer,e=>Su({type:`page-manifest`,pages:e})),o=mu(e,n,new Map(r),t.options);o.figKiwiVersion=i,o.figSchemaDeflated=a,yu=t.options?.populate===`first-page`?o:void 0,Su({type:`graph`,graph:gu(o)})}catch(e){Su({type:`graph`,error:e instanceof Error?e.message:String(e)})}}})();