/* Test-only syscall interposition. Never linked into the registry binary.
 * Select one operation/path in an isolated fixture directory, then inject an
 * errno before it or stop after it so the harness can kill the real process. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdatomic.h>

#ifdef __APPLE__
#define ORIGINAL(name) name
#else
#define ORIGINAL(name) dlsym(RTLD_NEXT,#name)
#endif

static atomic_int fired;
static int fd_path(int fd,char *path) {
#ifdef __APPLE__
  return fcntl(fd,F_GETPATH,path)==0;
#else
  char link[64]; snprintf(link,sizeof link,"/proc/self/fd/%d",fd);
  ssize_t n=readlink(link,path,PATH_MAX-1); if(n<0)return 0; path[n]=0; return 1;
#endif
}
static int selected(const char *operation,int fd,const char *suffix) {
  const char *op=getenv("REGISTRY_TEST_OP"),*root=getenv("REGISTRY_TEST_ROOT"),*match=getenv("REGISTRY_TEST_MATCH");
  if(!op||!root||!match||strcmp(op,operation))return 0;
  char path[PATH_MAX]; if(!fd_path(fd,path))return 0;
  if(strncmp(path,root,strlen(root)))return 0;
  char whole[PATH_MAX*2]; snprintf(whole,sizeof whole,"%s/%s",path,suffix?suffix:"");
  if(!strstr(whole,match))return 0;
  int expected=0; return atomic_compare_exchange_strong(&fired,&expected,1);
}
static void marker(void) {
  const char *path=getenv("REGISTRY_TEST_MARKER"); if(!path)return;
  int fd=open(path,O_WRONLY|O_CREAT|O_EXCL,0600); if(fd>=0)close(fd);
}
static int failure(void) {
  const char *error=getenv("REGISTRY_TEST_ERRNO");
  if(!error)return 0;
  marker(); errno=atoi(error); return 1;
}
static void stopped(void) { marker(); raise(SIGSTOP); }

static int test_fsync(int fd) {
  int (*real)(int)=ORIGINAL(fsync); int hit=selected("fsync",fd,NULL);
  if(hit&&failure())return -1; int result=real(fd); if(hit&&result==0)stopped(); return result;
}
static int test_linkat(int a,const char *p,int b,const char *q,int flags) {
  int (*real)(int,const char*,int,const char*,int)=ORIGINAL(linkat); int hit=selected("linkat",b,q);
  if(hit&&failure())return -1; int result=real(a,p,b,q,flags); if(hit&&result==0)stopped(); return result;
}
static int test_renameat(int a,const char *p,int b,const char *q) {
  int (*real)(int,const char*,int,const char*)=ORIGINAL(renameat); int hit=selected("renameat",b,q);
  if(hit&&failure())return -1; int result=real(a,p,b,q); if(hit&&result==0)stopped(); return result;
}
static int test_unlinkat(int a,const char *p,int flags) {
  int (*real)(int,const char*,int)=ORIGINAL(unlinkat); int hit=selected("unlinkat",a,p);
  if(hit&&failure())return -1; int result=real(a,p,flags); if(hit&&result==0)stopped(); return result;
}
#ifdef __APPLE__
#define INTERPOSE(new,old) __attribute__((used)) static struct { const void *replacement; const void *original; } pair_##old __attribute__((section("__DATA,__interpose"))) = { (void *)&new, (void *)&old };
INTERPOSE(test_fsync,fsync)
INTERPOSE(test_linkat,linkat)
INTERPOSE(test_renameat,renameat)
INTERPOSE(test_unlinkat,unlinkat)
#else
int fsync(int fd){return test_fsync(fd);}
int linkat(int a,const char*p,int b,const char*q,int f){return test_linkat(a,p,b,q,f);}
int renameat(int a,const char*p,int b,const char*q){return test_renameat(a,p,b,q);}
int unlinkat(int a,const char*p,int f){return test_unlinkat(a,p,f);}
#endif
