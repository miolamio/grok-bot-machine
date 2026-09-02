/* MIT-SHM (fallback: XGetImage) PNG dump of the X root window to stdout.
 * Usage: xcapture [DISPLAY]
 * stderr: XCAPTURE_BACKEND=mit-shm|xgetimage
 */
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/extensions/XShm.h>
#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ipc.h>
#include <sys/shm.h>

static void die(const char *msg) {
    fprintf(stderr, "xcapture: %s\n", msg);
    exit(1);
}

static int write_png(unsigned char *rgb, int width, int height) {
    png_structp png = png_create_write_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    png_infop info;
    png_bytep *rows;
    int y;

    if (!png)
        return -1;
    info = png_create_info_struct(png);
    if (!info) {
        png_destroy_write_struct(&png, NULL);
        return -1;
    }
    if (setjmp(png_jmpbuf(png))) {
        png_destroy_write_struct(&png, &info);
        return -1;
    }
    png_init_io(png, stdout);
    png_set_compression_level(png, 1);
    png_set_filter(png, 0, PNG_FILTER_NONE);
    png_set_IHDR(png, info, (png_uint_32)width, (png_uint_32)height, 8,
                 PNG_COLOR_TYPE_RGB, PNG_INTERLACE_NONE,
                 PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
    png_write_info(png, info);
    rows = malloc((size_t)height * sizeof(*rows));
    if (!rows) {
        png_destroy_write_struct(&png, &info);
        return -1;
    }
    for (y = 0; y < height; y++)
        rows[y] = rgb + (size_t)y * (size_t)width * 3;
    png_write_image(png, rows);
    png_write_end(png, NULL);
    free(rows);
    png_destroy_write_struct(&png, &info);
    return 0;
}

static void ximage_to_rgb(XImage *img, unsigned char *rgb) {
    int x, y;
    unsigned long rmask = img->red_mask ? img->red_mask : 0xff0000;
    unsigned long gmask = img->green_mask ? img->green_mask : 0x00ff00;
    unsigned long bmask = img->blue_mask ? img->blue_mask : 0x0000ff;
    int rshift = 0, gshift = 0, bshift = 0;
    unsigned long m;
    int bpp = img->bits_per_pixel;

    for (m = rmask; m && (m & 1) == 0; m >>= 1)
        rshift++;
    for (m = gmask; m && (m & 1) == 0; m >>= 1)
        gshift++;
    for (m = bmask; m && (m & 1) == 0; m >>= 1)
        bshift++;

    /* Fast path: 32-bit BGRA/ARGB packed pixels (Xvfb default). */
    if (bpp == 32 && img->byte_order == LSBFirst && rmask == 0xff0000 && bmask == 0x0000ff) {
        for (y = 0; y < img->height; y++) {
            unsigned char *src = (unsigned char *)img->data + (size_t)y * (size_t)img->bytes_per_line;
            unsigned char *d = rgb + (size_t)y * (size_t)img->width * 3;
            for (x = 0; x < img->width; x++) {
                d[0] = src[2];
                d[1] = src[1];
                d[2] = src[0];
                d += 3;
                src += 4;
            }
        }
        return;
    }

    for (y = 0; y < img->height; y++) {
        for (x = 0; x < img->width; x++) {
            unsigned long p = XGetPixel(img, x, y);
            unsigned char *d = rgb + ((size_t)y * (size_t)img->width + (size_t)x) * 3;
            d[0] = (unsigned char)((p & rmask) >> rshift);
            d[1] = (unsigned char)((p & gmask) >> gshift);
            d[2] = (unsigned char)((p & bmask) >> bshift);
        }
    }
}

int main(int argc, char **argv) {
    Display *dpy;
    Window root;
    XWindowAttributes attr;
    XImage *img = NULL;
    XShmSegmentInfo shminfo;
    unsigned char *rgb;
    int use_shm = 0;
    size_t npix;

    memset(&shminfo, 0, sizeof(shminfo));
    dpy = XOpenDisplay(argc > 1 ? argv[1] : NULL);
    if (!dpy)
        die("cannot open display");

    root = DefaultRootWindow(dpy);
    if (!XGetWindowAttributes(dpy, root, &attr))
        die("XGetWindowAttributes failed");
    if (attr.width <= 0 || attr.height <= 0)
        die("bad root size");

    if (XShmQueryExtension(dpy)) {
        img = XShmCreateImage(dpy, DefaultVisual(dpy, DefaultScreen(dpy)),
                              (unsigned)DefaultDepth(dpy, DefaultScreen(dpy)),
                              ZPixmap, NULL, &shminfo, (unsigned)attr.width,
                              (unsigned)attr.height);
        if (img) {
            shminfo.shmid = shmget(IPC_PRIVATE,
                                   (size_t)img->bytes_per_line * (size_t)img->height,
                                   IPC_CREAT | 0600);
            if (shminfo.shmid >= 0) {
                shminfo.shmaddr = img->data = shmat(shminfo.shmid, NULL, 0);
                shminfo.readOnly = False;
                if (shminfo.shmaddr != (char *)-1 && XShmAttach(dpy, &shminfo)) {
                    shmctl(shminfo.shmid, IPC_RMID, NULL);
                    if (XShmGetImage(dpy, root, img, 0, 0, AllPlanes))
                        use_shm = 1;
                }
            }
        }
        if (!use_shm) {
            if (img) {
                if (shminfo.shmaddr && shminfo.shmaddr != (char *)-1)
                    shmdt(shminfo.shmaddr);
                XDestroyImage(img);
                img = NULL;
            }
        }
    }

    if (!use_shm) {
        img = XGetImage(dpy, root, 0, 0, (unsigned)attr.width, (unsigned)attr.height,
                        AllPlanes, ZPixmap);
        if (!img)
            die("XGetImage failed");
    }

    npix = (size_t)img->width * (size_t)img->height;
    rgb = malloc(npix * 3);
    if (!rgb)
        die("oom");
    ximage_to_rgb(img, rgb);

    fprintf(stderr, "XCAPTURE_BACKEND=%s\n", use_shm ? "mit-shm" : "xgetimage");
    if (write_png(rgb, img->width, img->height) != 0)
        die("png write failed");

    free(rgb);
    if (use_shm) {
        XShmDetach(dpy, &shminfo);
        shmdt(shminfo.shmaddr);
        img->data = NULL;
    }
    XDestroyImage(img);
    XCloseDisplay(dpy);
    return 0;
}
