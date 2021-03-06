import torch
import torch.optim as optim
import torch.nn as nn
from torch.autograd import Variable
import numpy as np
import pdb

def dragan_penalty(X, discriminator, gpu_id):
    #untested
    lambda_ = 10
    batch_size = X.size(0)
    
    # gradient penalty
    alpha = torch.rand(batch_size, 1).expand(X.size()).cuda(gpu_id)
    alpha2 = torch.rand(batch_size, 1).expand(X.size()).cuda(gpu_id)
    
    pdb.set_trace()
    
    x_hat = Variable(alpha * X.data + (1 - alpha) * (X.data + 0.5 * X.data.std() * alpha2), requires_grad=True)
    pred_hat = discriminator(x_hat)
    gradients = grad(outputs=pred_hat, inputs=x_hat, grad_outputs=torch.ones(pred_hat.size()).cuda(gpu_id), create_graph=True, retain_graph=True, only_inputs=True)[0]
    gradient_penalty = lambda_ * ((gradients.norm(2, dim=1) - 1) ** 2).mean()
    gradient_penalty.backward()

def iteration(enc, dec, encD, decD, 
              optEnc, optDec, optEncD, optDecD, 
              critRecon, critZClass, critZRef, critEncD, critDecD,
              dataProvider, opt):
    gpu_id = opt.gpu_ids[0]
    
    
    tmp = decD
    decD = enc

    rand_inds_encD = np.random.permutation(opt.ndat)
    niter = len(range(0, len(rand_inds_encD), opt.batch_size))
    inds_encD = (rand_inds_encD[i:i+opt.batch_size] for i in range(0, len(rand_inds_encD), opt.batch_size))

    inds = next(inds_encD)
    x = Variable(dataProvider.get_images(inds,'train')).cuda(gpu_id)
    
    if opt.nClasses > 0:
        classes = Variable(dataProvider.get_classes(inds,'train')).cuda(gpu_id)
    
    if opt.nRef > 0:
        ref = Variable(dataProvider.get_ref(inds,'train')).cuda(gpu_id)

    ###update the discriminator
    #maximize log(AdvZ(z)) + log(1 - AdvZ(Enc(x)))
    for p in encD.parameters(): # reset requires_grad
        p.requires_grad = True # they are set to False below in netG update

    # for p in decD.parameters():
    #     p.requires_grad = True

    for p in enc.parameters():
        p.requires_grad = False

    for p in dec.parameters():
        p.requires_grad = False
    
    optEnc.zero_grad()
    optDec.zero_grad()
    optEncD.zero_grad()
    optDecD.zero_grad()   
        
    zAll, yHat_xReal = enc(x)
    
    for var in zAll:
        var.detach_()
    
    xHat = dec(zAll)
  
    zReal = Variable(opt.latentSample(opt.batch_size, opt.nlatentdim)).cuda(gpu_id)
    zFake = zAll[-1]

    
    ### train encD
    y_zReal = Variable(torch.ones(opt.batch_size)).cuda(gpu_id)
    y_zFake = Variable(torch.zeros(opt.batch_size)).cuda(gpu_id)
    
    # train with real
    yHat_zReal = encD(zReal)
    errEncD_real = critEncD(yHat_zReal, y_zReal)
    errEncD_real.backward(retain_graph=True)

    # train with fake
    yHat_zFake = encD(zFake)
    errEncD_fake = critEncD(yHat_zFake, y_zFake)
    errEncD_fake.backward(retain_graph=True)
    
    encDLoss = (errEncD_real + errEncD_fake)/2
    
    if opt.dragan:
        dragan_penalty(zReal, encD, gpu_id)
    
    optEncD.step()
    
    ###Train decD 
    for p in enc.parameters():
        p.requires_grad = True
    
    optEnc.zero_grad()
    
    if opt.nClasses > 0:
        y_xReal = classes
        y_xFake = Variable(torch.LongTensor(opt.batch_size).fill_(opt.nClasses)).cuda(gpu_id)
    else:
        y_xReal = Variable(torch.ones(opt.batch_size)).cuda(gpu_id)
        y_xFake = Variable(torch.zeros(opt.batch_size)).cuda(gpu_id)
    
    _, yHat_xReal = decD(x)
    errDecD_real = critDecD(yHat_xReal, y_xReal)
    # errDecD_real.backward(retain_graph=True)

    #train with fake, reconstructed
    _, yHat_xFake = decD(xHat.detach())   
    errDecD_fake = critDecD(yHat_xFake, y_xFake)
    # errDecD_fake.backward(retain_graph=True)
    
    #train with fake, sampled and decoded
    zAll[-1] = zReal
    
    # for var in zAll:
    #     var.detach_()
    
    _, yHat_xFake2 = decD(dec(zAll))
    errDecD_fake2 = critDecD(yHat_xFake2, y_xFake)
    # errEncD_fake2.backward(retain_graph=True)
    
    # pdb.set_trace()
    
    decDLoss = (errDecD_real + (errDecD_fake + errDecD_fake2)/2)/2
    decDLoss.backward(retain_graph=True)
    
    if opt.dragan:
        dragan_penalty(x, decD, gpu_id)
    
    optEnc.step()

    for p in enc.parameters():
        p.requires_grad = True

    for p in dec.parameters():
        p.requires_grad = True  

    for p in encD.parameters():
        p.requires_grad = False

    # for p in decD.parameters():
    #     p.requires_grad = False

    optEnc.zero_grad()
    optDec.zero_grad()
    optEncD.zero_grad()  
    optDecD.zero_grad()          

    ## train the autoencoder
    zAll, _ = enc(x)
    xHat = dec(zAll)    
    
    c = 0      
    if opt.nClasses > 0:
        classLoss = critZClass(zAll[c], classes)
        classLoss.backward(retain_graph=True)        
        c += 1
        
    if opt.nRef > 0:
        refLoss = critZRef(zAll[c], ref)
        refLoss.backward(retain_graph=True)        
        c += 1
        
    reconLoss = critRecon(xHat, x)
    reconLoss.backward(retain_graph=True)        

    #update wrt encD
    yHatFake = encD(zAll[-1])
    minimaxEncDLoss = critEncD(yHatFake, y_zReal)
    (minimaxEncDLoss.mul(opt.encDRatio)).backward(retain_graph=True)

    optEnc.step()
    
    for p in enc.parameters():
        p.requires_grad = False
    
    #update wrt decD(dec(enc(X)))
    _, yHat_xFake = decD(xHat)
    minimaxDecDLoss = critDecD(yHat_xFake, y_xReal)
    
    #update wrt decD(dec(Z))
    zAll[-1] = Variable(opt.latentSample(opt.batch_size, opt.nlatentdim)).cuda(gpu_id)
    
    for var in zAll:
        var.detach_()
    
    xHat = dec(zAll)

    _, yHat_xFake2 = decD(xHat)
    minimaxDecDLoss2 = critDecD(yHat_xFake2, y_xReal)
    
    minimaxDecLoss = (minimaxDecDLoss+minimaxDecDLoss2)/2
    (minimaxDecLoss.mul(opt.decDRatio)).backward(retain_graph=True)
    
    optDec.step()

    
    errors = (reconLoss.data[0],)
    if opt.nClasses > 0:
        errors += (classLoss.data[0],)
    
    if opt.nRef > 0:
        errors += (refLoss.data[0],)
    
    errors += (minimaxEncDLoss.data[0], encDLoss.data[0], minimaxDecLoss.data[0], decDLoss.data[0])
    
    decD = tmp
    return errors, zFake.data
    