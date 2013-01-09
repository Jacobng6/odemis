#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 14 Sep 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
from odemis import model
from odemis.dataio import tiff
from unittest.case import skip
import Image
import libtiff
import libtiff.libtiff_ctypes as T # for the constant names
import numpy
import os
import time
import unittest

FILENAME = "test" + tiff.EXTENSIONS[0] 
class TestTiffIO(unittest.TestCase):
    
    # Be careful: numpy's notation means that the pixel coordinates are Y,X,C
    def testExportOnePage(self):
        # create a simple greyscale image
        size = (256, 512)
        dtype = numpy.uint16
        data = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
        white = (12, 52) # non symmetric position
        # less that 2**15 so that we don't have problem with PIL.getpixel() always returning an signed int
        data[white[-1:-3:-1]] = 124
        
        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        self.assertEqual(im.size, size)
        self.assertEqual(im.getpixel(white), 124)
        
        os.remove(FILENAME)

#    @skip("Doesn't work")
    def testExportMultiPage(self):
        # create a simple greyscale image
        size = (512, 256)
        white = (12, 52) # non symmetric position
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            a = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype))
            a[white[-1:-3:-1]] = 124
            ldata.append(a)

        # export
        tiff.export(FILENAME, ldata)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # check the number of pages
        for i in range(num):
            im.seek(i)
            self.assertEqual(im.size, size)
            self.assertEqual(im.getpixel(white), 124)
            
        os.remove(FILENAME)

    def testExportThumbnail(self):
        # create a simple greyscale image
        size = (512, 256)
        dtype = numpy.uint16
        ldata = []
        num = 2
        for i in range(num):
            ldata.append(model.DataArray(numpy.zeros(size[-1:-3:-1], dtype)))

        # thumbnail : small RGB completely red
        tshape = (size[1]//8, size[0]//8, 3)
        tdtype = numpy.uint8
        thumbnail = numpy.zeros(tshape, tdtype)
        thumbnail[:, :, 0] += 255 # red
        blue = (12, 22) # non symmetric position
        thumbnail[blue[-1:-3:-1]] = [0,0,255]
        
        # export
        tiff.export(FILENAME, ldata, thumbnail)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        im = Image.open(FILENAME)
        self.assertEqual(im.format, "TIFF")
        
        # first page should be thumbnail
        im.seek(0)
        self.assertEqual(im.size, (tshape[1], tshape[0]))
        self.assertEqual(im.getpixel((0,0)), (255,0,0))
        self.assertEqual(im.getpixel(blue), (0,0,255))
        
        # check the number of pages
        for i in range(num):
            im.seek(i+1)
            self.assertEqual(im.size, size)
            
        os.remove(FILENAME)
    
    def testMetadata(self):
        """
        checks that the metadata is saved with every picture
        """
        size = (512, 256, 1)
        dtype = numpy.dtype("uint16")
        metadata = {model.MD_SW_VERSION: "1.0-test",
                    model.MD_HW_NAME: "fake hw",
                    model.MD_DESCRIPTION: "test",
                    model.MD_ACQ_DATE: time.time(),
                    model.MD_BPP: 12,
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    }
        
        data = model.DataArray(numpy.zeros(size[-1:-3:-1], dtype), metadata=metadata)     
        
        # export
        tiff.export(FILENAME, data)
        
        # check it's here
        st = os.stat(FILENAME) # this test also that the file is created
        self.assertGreater(st.st_size, 0)
        imo = libtiff.tiff.TIFFfile(FILENAME)
        self.assertEqual(len(imo.IFD), 1, "Tiff file doesn't contain just one image")

        ifd = imo.IFD[0]
        # check format        
        self.assertEqual(size[2], ifd.get_value("SamplesPerPixel"))
        # BitsPerSample is the actual format, not model.MD_BPP
        self.assertEqual(dtype.itemsize * 8, ifd.get_value("BitsPerSample")[0])
        self.assertEqual(T.SAMPLEFORMAT_UINT, ifd.get_value("SampleFormat")[0])
        
        # check metadata
        self.assertEqual("Odemis " + metadata[model.MD_SW_VERSION], ifd.get_value("Software"))
        self.assertEqual(metadata[model.MD_HW_NAME], ifd.get_value("Make"))
        self.assertEqual(metadata[model.MD_DESCRIPTION], ifd.get_value("PageName"))
        yres = rational2float(ifd.get_value("YResolution"))
        self.assertAlmostEqual(1 / metadata[model.MD_PIXEL_SIZE][1], yres * 100)
        ypos = rational2float(ifd.get_value("YPosition"))
        self.assertAlmostEqual(metadata[model.MD_POS][1], (ypos / 100) - 1)
        
        
def rational2float(rational):
    """
    Converts a rational number (from libtiff) to a float
    rational (numpy array of shape 1 with numer and denom fields): num,denom
    """
    return rational["numer"][0]/rational["denom"][0]

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()