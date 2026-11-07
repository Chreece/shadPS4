// SPDX-License-Identifier: GPL-2.0-or-later
#define _DEFAULT_SOURCE
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "shaders.h"
#include "registers.h"

extern int sceKernelAllocateDirectMemory(int64_t,int64_t,size_t,size_t,int,int64_t*);
extern int sceKernelMapDirectMemory(void**,size_t,int,int,int64_t,size_t);
extern int64_t sceKernelGetDirectMemorySize(void);
extern uint32_t sceGnmDrawInitDefaultHardwareState350(uint32_t*,uint32_t);
extern int sceGnmSetVsShader(uint32_t*,uint32_t,const uint32_t*,uint32_t);
extern int sceGnmSetPsShader(uint32_t*,uint32_t,const uint32_t*);
extern int sceGnmDrawIndexAuto(uint32_t*,uint32_t,uint32_t,uint32_t);
extern int sceGnmSubmitCommandBuffers(uint32_t,void**,uint32_t*,void**,uint32_t*);
extern int sceGnmSubmitDone(void);

static uint32_t* cmd;
static unsigned used;
static uint32_t bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
static uint64_t ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (uint64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
static void regs(uint32_t address,const uint32_t* values,unsigned count){
    uint32_t base,opcode;
    if(address>=0xc000){base=0xc000;opcode=0x79;}
    else if(address>=0xa000){base=0xa000;opcode=0x69;}
    else {base=0x2c00;opcode=0x76;}
    cmd[used++]=0xc0000000u|(count<<16)|(opcode<<8);
    cmd[used++]=address-base;
    memcpy(cmd+used,values,count*4);used+=count;
}
static void reg(uint32_t address,uint32_t value){regs(address,&value,1);}
static void scissor(uint32_t address){uint32_t s[]={0x80000000u,128u|(128u<<16)};regs(address,s,2);}


static uint8_t* vs;
static uint8_t* ps;
static float* vertices;
static volatile uint32_t* fence;
static int draw(uint32_t* pixels,unsigned slices,unsigned first_slice){
    *fence=0;
        used=sceGnmDrawInitDefaultHardwareState350(cmd,256);
        if(!used||used>256)return 4;
        uint32_t vr[]={ (uint32_t)((uintptr_t)vs>>8),0,0x000c0001,5u<<1,0,0x44,(1u<<19)|(1u<<21)};
        uint32_t pr[]={ (uint32_t)((uintptr_t)ps>>8),0,0x000c0000,0,0,9,1u<<8,1u<<8,0,0,0,15};
        if(sceGnmSetVsShader(cmd+used,29,vr,0)<0){return 5;}used+=29;
        if(sceGnmSetPsShader(cmd+used,40,pr)<0){return 6;}used+=40;
        uint64_t vertex_address=(uintptr_t)vertices;
        uint32_t user[]={ (uint32_t)vertex_address,(uint32_t)(vertex_address>>32)&255,48,0x00027fac,0};
        regs(0x2c4c,user,5);
        uint32_t color[13]={ (uint32_t)((uintptr_t)pixels>>8),15,255,first_slice|((slices-1)<<13),(10u<<2)|(1u<<15),8,0,0,0,0,0,0,0};
        regs(R_color_buffers,color,13);reg(R_color_target_mask,15);reg(R_color_control,0x00cc0010);
        reg(R_depth_control,0);reg(R_blend_control,0);reg(R_stage_enable,0);
        reg(R_polygon_control,0);reg(R_mode_control,2);reg(R_aa_config,0);
        reg(R_primitive_type,4);
        reg(0xc24d,1);
        reg(0xa280,0x00400040);reg(0xa281,0xffff0000);
        reg(R_window_offset,0);
        uint32_t screen[]={0,128u|(128u<<16)};regs(R_screen_scissor,screen,2);
        scissor(R_window_scissor);scissor(R_generic_scissor);
        for(unsigned i=0;i<16;i++){
            scissor(R_viewport_scissors+i*2);
            uint32_t depth[]={bits(0),bits(1)};regs(R_viewport_depths+i*2,depth,2);
            const float xs=i==0?32.f:0.f;
            const float ys=32.f;
            uint32_t vp[]={bits(xs),bits(64.5f),bits(ys),bits(64.5f),bits(.5f),bits(.5f)};
            regs(R_viewports+i*6,vp,6);
        }
        reg(R_viewport_control,0x3fu);
        reg(R_clipper_control,1u<<19);
        if(sceGnmDrawIndexAuto(cmd+used,7,3,0)<0){return 7;}used+=7;
        uint64_t address=(uintptr_t)fence;
        cmd[used++]=0xc0044700;cmd[used++]=0x514;
        cmd[used++]=(uint32_t)address;cmd[used++]=(uint32_t)(address>>32)|(1u<<29);
        cmd[used++]=1;cmd[used++]=0;
        if(used>0x1000){return 8;}
        __sync_synchronize();void* buffers[]={cmd};uint32_t lengths[]={used*4};
        if(sceGnmSubmitCommandBuffers(1,buffers,lengths,0,0)<0 || sceGnmSubmitDone()<0){return 9;}
        uint64_t start=ms();while(*fence!=1 && ms()-start<2000)usleep(1000);
        if(*fence!=1){return 10;}
        __sync_synchronize();
    return 0;
}

int main(void){
    const size_t allocation_size=0x400000,layer_bytes=0x10000;
    int64_t physical=0;void* allocation=0;
    if(sceKernelAllocateDirectMemory(0,sceKernelGetDirectMemorySize(),allocation_size,0x4000,3,&physical)<0 ||
       sceKernelMapDirectMemory(&allocation,allocation_size,0x33,0,physical,0x4000)<0)return 1;
    uint8_t* memory=allocation;cmd=(uint32_t*)memory;
    vs=memory+0x4000;ps=memory+0x8000;vertices=(float*)(memory+0xc000);
    fence=(volatile uint32_t*)(memory+0x10000);
    if(((uintptr_t)memory+allocation_size)>>40)return 2;
    memcpy(vs,viewport_vs,sizeof(viewport_vs));memcpy(ps,white_ps,sizeof(white_ps));
    const float tri[]={-.75f,-.75f,.5f,1.f, .75f,-.75f,.5f,1.f, 0.f,.75f,.5f,1.f};
    memcpy(vertices,tri,sizeof(tri));
    FILE* log=fopen("/data/subresource-alias-probe.log","w");if(!log)return 3;
    uint64_t hashes[4];unsigned controls[4]={0};
    for(unsigned test=0;test<4;++test){
        uint32_t* pixels=(uint32_t*)(memory+0x20000+test*8*layer_bytes);
        memset(pixels,0,8*layer_bytes);
        int rc=draw(pixels,8,7);
        unsigned target=3+test/2;
        if(!rc)rc=(test%2)?draw(pixels+3*layer_bytes/4,2,test/2):draw(pixels,8,target);
        if(rc){fprintf(log,"STOP case=%u error=%d; no further submissions\n",test,rc);fclose(log);return rc;}
        uint64_t hash=14695981039346656037ull;
        for(unsigned layer=0;layer<8;++layer){
            unsigned lit=0;
            for(unsigned i=0;i<layer_bytes/4;++i){
                uint32_t value=((volatile uint32_t*)pixels)[layer*layer_bytes/4+i];
                hash=(hash^value)*1099511628211ull;lit+=value!=0;
            }
            if(layer==target)controls[test]=lit;
            fprintf(log,"case=%u layer=%u lit=%u\n",test,layer,lit);
        }
        hashes[test]=hash;
        char path[96];snprintf(path,sizeof(path),"/data/subresource-alias-%u.rgba",test);
        FILE* raw=fopen(path,"wb");if(raw){fwrite(pixels,1,8*layer_bytes,raw);fclose(raw);}
        fflush(log);
    }
    fprintf(log,"base3_pair_equal=%u base3_plus_view1_pair_equal=%u controls_nonempty=%u\n",
            hashes[0]==hashes[1],hashes[2]==hashes[3],controls[0]!=0 && controls[2]!=0);
    fputs("OBSERVATIONS_COMPLETE; hardware comparison required\n",log);fclose(log);return 0;
}
