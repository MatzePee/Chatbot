// Match the generated patch to the original lighting at every mask boundary.
// A coarse-to-fine harmonic extension carries the exterior RGB difference
// into the hole without mixing the removed object's pixels back into it.
export function matchMaskBoundary(original, generated, mask, width, height) {
  const levels=[]
  let w=width,h=height
  let weights=new Float32Array(w*h)
  let colors=new Float32Array(w*h*3)
  for(let i=0;i<w*h;i++){
    if(mask[i*4]>0)continue
    weights[i]=1
    for(let c=0;c<3;c++){
      colors[i*3+c]=Math.max(-64,Math.min(64,original[i*4+c]-generated[i*4+c]))
    }
  }
  while(true){
    levels.push({w,h,colors,weights})
    if(w===1 && h===1)break
    const nw=Math.ceil(w/2),nh=Math.ceil(h/2)
    const cw=new Float32Array(nw*nh), cc=new Float32Array(nw*nh*3)
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){
      const i=y*w+x,j=(y>>1)*nw+(x>>1)
      cw[j]+=weights[i]
      for(let c=0;c<3;c++)cc[3*j+c]+=weights[i]*colors[3*i+c]
    }
    for(let j=0;j<cw.length;j++)if(cw[j]){
      for(let c=0;c<3;c++)cc[3*j+c]/=cw[j]
      cw[j]/=4
    }
    w=nw;h=nh;weights=cw;colors=cc
  }
  for(let level=levels.length-2;level>=0;level--){
    const fine=levels[level],coarse=levels[level+1]
    const {w,h,colors,weights}=fine
    for(let y=0;y<h;y++)for(let x=0;x<w;x++){
      const i=y*w+x
      if(weights[i])continue
      const j=(y>>1)*coarse.w+(x>>1)
      for(let c=0;c<3;c++)colors[3*i+c]=coarse.colors[3*j+c]
    }
    // Red-black relaxation on each scale keeps independent islands and holes.
    for(let pass=0;pass<8;pass++)for(let y=0;y<h;y++){
      for(let x=(pass+y)%2;x<w;x+=2){
        const i=y*w+x
        if(weights[i])continue
        const left=x?i-1:i,right=x+1<w?i+1:i,up=y?i-w:i,down=y+1<h?i+w:i
        for(let c=0;c<3;c++)colors[3*i+c]=(colors[3*left+c]+colors[3*right+c]+colors[3*up+c]+colors[3*down+c])*.25
      }
    }
  }
  const correction=levels[0].colors
  for(let i=0;i<width*height;i++)if(mask[4*i]){
    for(let c=0;c<3;c++)generated[4*i+c]+=correction[3*i+c]
  }
}
