# Copyright (c) 2012, GPy authors (see AUTHORS.txt).
# Licensed under the BSD 3-clause license (see LICENSE.txt)

import numpy as np
from ..core.parameterization import Parameterized, Param
from paramz.transformations import Logexp


class WarpingFunction(Parameterized):
    """
    abstract function for warping
    z = f(y)
    """

    def __init__(self, name):
        super(WarpingFunction, self).__init__(name=name)

    def f(self, y, psi):
        """function transformation
        y is a list of values (GP training data) of shape [N, 1]
        """
        raise NotImplementedError

    def fgrad_y(self, y, psi):
        """gradient of f w.r.t to y"""
        raise NotImplementedError

    def fgrad_y_psi(self, y, psi):
        """gradient of f w.r.t to y"""
        raise NotImplementedError

    def f_inv(self, z, psi):
        """inverse function transformation"""
        raise NotImplementedError

    def _get_param_names(self):
        raise NotImplementedError

    def plot(self, xmin, xmax):
        psi = self.psi
        y = np.arange(xmin, xmax, 0.01)
        f_y = self.f(y)
        from matplotlib import pyplot as plt
        plt.figure()
        plt.plot(y, f_y)
        plt.xlabel('y')
        plt.ylabel('f(y)')
        plt.title('warping function')
        plt.show()


class TanhFunction(WarpingFunction):

    def __init__(self, n_terms=3, initial_y=None):
        """
        n_terms specifies the number of tanh terms to be used
        """
        self.n_terms = n_terms
        self.num_parameters = 3 * self.n_terms + 1
        self.psi = np.ones((self.n_terms, 3))

        super(TanhFunction, self).__init__(name='warp_tanh')
        self.psi = Param('psi', self.psi)
        self.psi[:, :2].constrain_positive()

        self.d = Param('%s' % ('d'), 1.0, Logexp())
        self.link_parameter(self.psi)
        self.link_parameter(self.d)
        self.initial_y = initial_y

    def f(self, y):
        """
        Transform y with f using parameter vector psi
        psi = [[a,b,c]]

        :math:`f = (y * d) + \\sum_{terms} a * tanh(b *(y + c))`
        """
        d = self.d
        mpsi = self.psi
        z = d * y.copy()
        for i in range(len(mpsi)):
            a, b, c = mpsi[i]
            z += a * np.tanh(b * (y + c))
        return z

    def f_inv(self, z, max_iterations=100, y=None):
        """
        calculate the numerical inverse of f

        :param max_iterations: maximum number of N.R. iterations
        """

        z = z.copy()
        
        if y is None:
            # The idea here is to initialize y with +1 where
            # z is positive and -1 where it is negative.
            # For negative z, Newton-Raphson diverges 
            # if we initialize y with a positive value (and vice-versa).
            y = ((z > 0) * 1.) - (z <= 0)
            if self.initial_y is not None:
                y *= self.initial_y

        it = 0
        update = np.inf

        while it == 0 or (np.abs(update).sum() > 1e-10 and it < max_iterations):
            fy = self.f(y)
            fgrady = self.fgrad_y(y)
            update = (fy - z) / fgrady
            y -= update
            it += 1
        if it == max_iterations:
            print("WARNING!!! Maximum number of iterations reached in f_inv ")
            print("Sum of updates: %.4f" % np.sum(update))
        return y

    def fgrad_y(self, y, return_precalc=False):
        """
        gradient of f w.r.t to y ([N x 1])

        :returns: Nx1 vector of derivatives, unless return_precalc is true, 
        then it also returns the precomputed stuff
        """
        d = self.d
        mpsi = self.psi

        # vectorized version
        S = (mpsi[:,1] * (y[:,:,None] + mpsi[:,2])).T
        R = np.tanh(S)
        D = 1 - (R ** 2)

        GRAD = (d + (mpsi[:,0:1][:,:,None] * mpsi[:,1:2][:,:,None] * D).sum(axis=0)).T

        if return_precalc:
            return GRAD, S, R, D

        return GRAD

    def fgrad_y_psi(self, y, return_covar_chain=False):
        """
        gradient of f w.r.t to y and psi

        :returns: NxIx4 tensor of partial derivatives
        """
        mpsi = self.psi

        w, s, r, d = self.fgrad_y(y, return_precalc=True)
        gradients = np.zeros((y.shape[0], y.shape[1], len(mpsi), 4))
        for i in range(len(mpsi)):
            a,b,c  = mpsi[i]
            gradients[:, :, i, 0] = (b * (1.0/np.cosh(s[i])) ** 2).T
            gradients[:, :, i, 1] = a * (d[i] - 2.0 * s[i] * r[i] * (1.0/np.cosh(s[i])) ** 2).T
            gradients[:, :, i, 2] = (-2.0 * a * (b ** 2) * r[i] * ((1.0 / np.cosh(s[i])) ** 2)).T
        gradients[:, :, 0, 3] = 1.0

        if return_covar_chain:
            covar_grad_chain = np.zeros((y.shape[0], y.shape[1], len(mpsi), 4))
            for i in range(len(mpsi)):
                a,b,c = mpsi[i]
                covar_grad_chain[:, :, i, 0] = (r[i]).T
                covar_grad_chain[:, :, i, 1] = (a * (y + c) * ((1.0 / np.cosh(s[i])) ** 2).T)
                covar_grad_chain[:, :, i, 2] = a * b * ((1.0 / np.cosh(s[i])) ** 2).T
            covar_grad_chain[:, :, 0, 3] = y
            return gradients, covar_grad_chain

        return gradients

    def _get_param_names(self):
        variables = ['a', 'b', 'c', 'd']
        names = sum([['warp_tanh_%s_t%i' % (variables[n],q) for n in range(3)] 
                     for q in range(self.n_terms)],[])
        names.append('warp_tanh')
        return names

    def update_grads(self, Y_untransformed, Kiy):
        grad_y = self.fgrad_y(Y_untransformed)
        grad_y_psi, grad_psi = self.fgrad_y_psi(Y_untransformed,
                                                return_covar_chain=True)
        djac_dpsi = ((1.0 / grad_y[:, :, None, None]) * grad_y_psi).sum(axis=0).sum(axis=0)
        dquad_dpsi = (Kiy[:, None, None, None] * grad_psi).sum(axis=0).sum(axis=0)

        warping_grads = -dquad_dpsi + djac_dpsi

        self.psi.gradient[:] = warping_grads[:, :-1]
        self.d.gradient[:] = warping_grads[0, -1]


class LogFunction(WarpingFunction):
    """
    Easy wrapper for applying a fixed warping function to
    positive-only values.
    """
    def __init__(self):
        self.num_parameters = 0
        super(LogFunction, self).__init__(name='log')

    def f(self, y):
        return np.log(y)

    def fgrad_y(self, y):
        return 1. / y

    def update_grads(self, Y_untransformed, Kiy):
        pass

    def fgrad_y_psi(self, y, return_covar_chain=False):
        if return_covar_chain:
            return 0, 0
        return 0

    def f_inv(self, z, y=None):
        return np.exp(z)


class IdentityFunction(WarpingFunction):
    """
    Identity warping function. This is for testing and sanity check purposes
    and should not be used in practice.
    """
    def __init__(self):
        self.num_parameters = 0
        super(IdentityFunction, self).__init__(name='identity')
        
    def f(self, y):
        return y

    def fgrad_y(self, y):
        return np.ones(y.shape)

    def update_grads(self, Y_untransformed, Kiy):
        pass

    def fgrad_y_psi(self, y, return_covar_chain=False):
        if return_covar_chain:
            return 0, 0
        return 0
        
    def f_inv(self, z, y=None):
        return z
